#!/usr/bin/env python3
"""Deterministic local CSV analysis and hypothetical position sizing."""
import csv
import io
import json
import math
import re
import statistics
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
HEADER_SCAN_ROWS = 30
PREVIEW_ROWS = 12
DATE_HEADERS = {'date', 'datetime'}
PRICE_HEADERS = ('adjusted close', 'adj close', 'close')


class ToolError(ValueError):
    pass


def normalized(value):
    return re.sub(r'[^a-z0-9]+', ' ', value.strip().lower()).strip()


def header_indexes(row):
    cells = [normalized(cell) for cell in row]
    date_index = next((i for i, cell in enumerate(cells) if cell in DATE_HEADERS), None)
    price_index = None
    price_name = None
    for alias in PRICE_HEADERS:
        if alias in cells:
            price_index = cells.index(alias)
            price_name = alias
            break
    return date_index, price_index, price_name


def find_header(rows):
    scan = rows[:HEADER_SCAN_ROWS]
    # Prefer a row that names both a date and recognized closing-price column.
    for i, row in enumerate(scan):
        date_i, price_i, _ = header_indexes(row)
        if date_i is not None and price_i is not None:
            return i
    # Common watchlist/export headers may contain no historical price series.
    known = {'symbol', 'ticker', 'description', 'last', 'mark', 'bid', 'ask', 'quantity', 'qty', 'underlying'}
    for i, row in enumerate(scan):
        cells = {normalized(cell) for cell in row}
        next_row_matches = i + 1 >= len(scan) or len(scan[i + 1]) == len(row)
        if len(row) >= 2 and cells & known and next_row_matches:
            return i
    # Generic CSV fallback: the header and first data row usually have the same
    # width, unlike the short metadata lines that precede broker exports.
    for i, row in enumerate(scan[:-1]):
        if len(row) >= 2 and any(cell.strip() for cell in row) and len(scan[i + 1]) == len(row):
            return i
    raise ToolError('Could not find a column header within the first 30 CSV rows.')


def parse_date(value, row_number):
    raw = value.strip()
    if not raw:
        raise ToolError(f'Row {row_number}: date is empty.')
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', raw):
            parsed = date.fromisoformat(raw)
        else:
            iso = raw[:-1] + '+00:00' if raw.endswith(('Z', 'z')) else raw
            try:
                parsed = datetime.fromisoformat(iso).date()
            except ValueError:
                parsed = None
            if parsed is None:
                parsed = None
                for fmt in ('%m/%d/%Y', '%m/%d/%y', '%m/%d/%Y %H:%M', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M'):
                    try:
                        parsed = datetime.strptime(raw, fmt).date()
                        break
                    except ValueError:
                        pass
                if parsed is None:
                    raise ValueError('unsupported date format')
    except ValueError as exc:
        raise ToolError(f'Row {row_number}: invalid date {raw!r}; use ISO or common US date format.') from exc
    return parsed


def parse_close(value, row_number):
    try:
        result = Decimal(value.strip().replace(',', '').replace('$', ''))
    except (InvalidOperation, AttributeError):
        raise ToolError(f'Row {row_number}: closing price {value!r} is invalid.')
    if not result.is_finite() or result <= 0:
        raise ToolError(f'Row {row_number}: closing price must be finite and greater than zero.')
    if result.adjusted() < -12 or result.adjusted() > 15 or len(result.as_tuple().digits) > 28:
        raise ToolError(f'Row {row_number}: closing price is outside the supported numeric range.')
    as_float = float(result)
    if not math.isfinite(as_float) or as_float <= 0:
        raise ToolError(f'Row {row_number}: closing price is outside the supported numeric range.')
    return as_float


def summarize_csv(path):
    csv_path = Path(path).expanduser()
    if not csv_path.is_file():
        raise ToolError('CSV path does not point to a readable file.')
    if csv_path.stat().st_size > MAX_CSV_BYTES:
        raise ToolError('CSV file exceeds the 10 MB limit.')
    try:
        content = csv_path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise ToolError(f'Could not read CSV as UTF-8: {exc}') from exc
    try:
        rows = list(csv.reader(io.StringIO(content), strict=True))
    except csv.Error as exc:
        raise ToolError(f'Malformed CSV: {exc}') from exc
    header_row = find_header(rows)
    headers = [cell.strip() for cell in rows[header_row]]
    if not headers or any(not name for name in headers):
        raise ToolError('The detected header contains an empty column name.')
    if len({normalized(name) for name in headers}) != len(headers):
        raise ToolError('The detected header contains duplicate column names.')
    data_rows = []
    for line_number, row in enumerate(rows[header_row + 1:], header_row + 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(headers):
            raise ToolError(f'Row {line_number}: found {len(row)} cells; expected {len(headers)}.')
        data_rows.append(row)
        if len(data_rows) > MAX_ROWS:
            raise ToolError('CSV contains more than 100,000 data rows.')

    result = {
        'headers': headers,
        'row_count': len(data_rows),
        'preview': [dict(zip(headers, row)) for row in data_rows[:PREVIEW_ROWS]],
        'metrics': None,
    }
    date_index, price_index, price_alias = header_indexes(headers)
    if date_index is None or price_index is None:
        result['text'] = f"CSV contains {len(data_rows)} rows and {len(headers)} columns. No date and recognized close-price column pair was found, so price metrics are unavailable."
        result['metadata'] = {
            'chosen_price_column': None,
            'asof': None,
            'assumptions': ['CSV values are summarized as supplied; no live market data or dividend-adjusted total return is inferred.'],
        }
        return result

    dated_prices = []
    previous = None
    for offset, row in enumerate(data_rows, header_row + 2):
        observed_date = parse_date(row[date_index], offset)
        if previous is not None and observed_date <= previous:
            reason = 'duplicate' if observed_date == previous else 'out of order'
            raise ToolError(f'Row {offset}: dates are {reason}; dates must be strictly ascending.')
        previous = observed_date
        dated_prices.append((observed_date, parse_close(row[price_index], offset)))
    if not dated_prices:
        raise ToolError('No historical price rows were found.')

    prices = [price for _, price in dated_prices]
    peak = prices[0]
    max_drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        max_drawdown = min(max_drawdown, price / peak - 1)
    daily_returns = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    date_gaps = [(dated_prices[i][0] - dated_prices[i - 1][0]).days for i in range(1, len(dated_prices))]
    median_gap = statistics.median(date_gaps) if date_gaps else None
    plausible_daily = median_gap is not None and median_gap <= 4
    metrics = {
        'simple_price_return_pct': (prices[-1] / prices[0] - 1) * 100,
        'max_drawdown_pct': max_drawdown * 100,
        'annualized_daily_return_volatility_pct': statistics.stdev(daily_returns) * math.sqrt(252) * 100 if len(daily_returns) >= 2 and plausible_daily else None,
        'sma20': sum(prices[-20:]) / 20 if len(prices) >= 20 else None,
    }
    if any(value is not None and not math.isfinite(value) for value in metrics.values()):
        raise ToolError('Price calculations exceeded the supported numeric range.')
    chosen_column = headers[price_index]
    result['metrics'] = metrics
    result['metadata'] = {
        'chosen_price_column': chosen_column,
        'price_column_alias': price_alias,
        'asof': dated_prices[-1][0].isoformat(),
        'median_calendar_gap_days': median_gap,
        'daily_frequency_plausible': plausible_daily,
        'assumptions': [
            'Simple price return uses the supplied closing-price series. An adjusted-close series may include dividend or other adjustments; its adjustment method is unverified, so dividend-inclusive total return is not established.',
            'Annualized volatility is shown only when the median calendar gap is at most four days. It uses sample standard deviation of consecutive returns and sqrt(252), assuming daily trading sessions.',
            'Maximum drawdown is measured from closing-price peaks.',
        ],
    }
    result['text'] = f"CSV contains {len(data_rows)} rows. Using {chosen_column} through {dated_prices[-1][0].isoformat()}: simple price return {metrics['simple_price_return_pct']:.2f}%, maximum drawdown {metrics['max_drawdown_pct']:.2f}%."
    return result


def decimal_field(payload, name):
    value = payload.get(name)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ToolError(f'{name} must be a finite number greater than zero.')
    if not number.is_finite() or number <= 0:
        raise ToolError(f'{name} must be a finite number greater than zero.')
    if number.adjusted() < -12 or number.adjusted() > 15 or len(number.as_tuple().digits) > 28:
        raise ToolError(f'{name} is outside the supported numeric range.')
    return number


def decimal_text(number):
    if number == 0:
        return '0'
    return format(number.normalize(), 'f')


def trade_plan(payload):
    symbol = str(payload.get('symbol', '')).strip().upper()
    if not symbol or len(symbol) > 20 or not re.fullmatch(r'[A-Z0-9.^_-]+', symbol) or not re.search(r'[A-Z0-9]', symbol):
        raise ToolError('symbol must be a short ticker containing letters, digits, dot, caret, underscore, or hyphen.')
    side = str(payload.get('side', '')).strip().lower()
    if side not in ('long', 'short'):
        raise ToolError('side must be long or short.')
    entry = decimal_field(payload, 'entry')
    stop = decimal_field(payload, 'stop')
    target = decimal_field(payload, 'target')
    risk_budget = decimal_field(payload, 'risk_budget')
    capital_cap = decimal_field(payload, 'capital_cap')
    if side == 'long' and not stop < entry < target:
        raise ToolError('For a long plan, stop must be below entry and target above entry.')
    if side == 'short' and not target < entry < stop:
        raise ToolError('For a short plan, target must be below entry and stop above entry.')

    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    budget_shares = (risk_budget / risk_per_share).to_integral_value(rounding=ROUND_FLOOR)
    capital_shares = (capital_cap / entry).to_integral_value(rounding=ROUND_FLOOR)
    shares = int(min(budget_shares, capital_shares))
    planned_risk = Decimal(shares) * risk_per_share
    notional = Decimal(shares) * entry
    reward_risk = reward_per_share / risk_per_share
    metrics = {
        'symbol': symbol,
        'side': side,
        'entry': decimal_text(entry),
        'stop': decimal_text(stop),
        'target': decimal_text(target),
        'risk_budget': decimal_text(risk_budget),
        'capital_cap': decimal_text(capital_cap),
        'risk_per_share': decimal_text(risk_per_share),
        'shares': shares,
        'planned_risk': decimal_text(planned_risk),
        'notional': decimal_text(notional),
        'reward_risk': decimal_text(reward_risk),
    }
    text = (f"Hypothetical {side} plan for {symbol}: {shares} shares at {decimal_text(entry)}, "
            f"stop {decimal_text(stop)}, target {decimal_text(target)}; planned risk {decimal_text(planned_risk)} "
            f"and notional {decimal_text(notional)}. This is a sizing calculation, not a recommendation. "
            'Stops are not guaranteed. Fees and slippage are excluded. Enter manually in thinkorswim; this tool has no broker API.')
    return {'text': text, 'metrics': metrics}


def handle(payload):
    if not isinstance(payload, dict):
        raise ToolError('Input must be a JSON object.')
    action = payload.get('action')
    if action == 'csv':
        path = payload.get('path')
        if not isinstance(path, str) or not path.strip():
            raise ToolError('CSV action requires a user-selected path string.')
        return summarize_csv(path)
    if action == 'trade_plan':
        return trade_plan(payload)
    raise ToolError('action must be csv or trade_plan.')


def main():
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(handle(payload), ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
