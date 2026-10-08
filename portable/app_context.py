"""Conversation-local app bookmarks; never store window handles or permissions."""
import re

APP_NAMES = {'discord':'Discord','brave':'Brave','chrome':'Chrome','safari':'Safari','edge':'Edge','firefox':'Firefox','webex':'Webex','zoom':'Zoom','teams':'Teams','finder':'Finder','textedit':'TextEdit','preview':'Preview','word':'Word','excel':'Excel','explorer':'File Explorer','notepad':'Notepad'}
_APPROVAL_RECOVERY = re.compile(r"^(?:(?:k|ok|okay|yes|sure|all right)[, ]+)?(?:i\s+)?(?:approve(?:\s+all)?|give\s+(?:you\s+)?permission(?:\s+to\s+(?:continue|proceed))?|authorize(?:\s+you)?|you\s+have\s+my\s+permission|you\s+can\s+(?:continue|proceed)|go\s+ahead|continue)(?:\s+with\s+(?:the\s+)?(?:task|app|browser|computer|previous task))?[.! ]*$", re.I)
_COURSE_PLATFORM = re.compile(
    r"\b(canvas|pearson|mcgraw\s*-?\s*hill|smart\s*book|connect\s+courseware|"
    r"(?:course|class|school|learning|student)\s+(?:site|website|portal|platform)|lms)\b",
    re.I,
)
_COURSE_ACTION = re.compile(
    r"\b(open|launch|go|navigate|visit|browse|start|continue|check|read|find|search|resume|"
    r"work|work through|do|complete|finish|answer|review|solve|practice|help me with)\b",
    re.I,
)
_COURSE_QUESTION = re.compile(
    r"^\s*(?:how|what|why|when|who|where|compare|explain|describe|should i|do you think|tell me how)\b",
    re.I,
)
_COURSE_NEGATION = re.compile(
    r"\b(?:do not|don't|never|avoid)\b.{0,60}\b(?:open|launch|go|navigate|visit|browse|work|continue|start)\b",
    re.I,
)
_BROWSER_APP_KEYS = {"brave", "chrome", "safari", "edge", "firefox"}


def _unquoted_user_text(text: str) -> str:
    return re.sub(r"(?s)(`[^`]*`|'[^']*'|\"[^\"]*\")", " ", text)


def is_course_platform_question(text: str) -> bool:
    """Keep informational course-platform questions in chat across platforms."""
    if not isinstance(text, str):
        return False
    clean = _unquoted_user_text(text).strip()
    return bool(_COURSE_QUESTION.search(clean) and _COURSE_PLATFORM.search(clean))


def is_course_platform_negation(text: str) -> bool:
    """Treat an explicit refusal to open or use a course site as chat intent."""
    if not isinstance(text, str):
        return False
    clean = _unquoted_user_text(text).strip()
    return bool(_COURSE_PLATFORM.search(clean) and _COURSE_NEGATION.search(clean))


def is_course_platform_request(text: str) -> bool:
    """Recognize explicit user requests to use a course site or supplied HTTPS link."""
    if not isinstance(text, str):
        return False
    clean = _unquoted_user_text(text).strip()
    if not clean or _COURSE_QUESTION.search(clean) or _COURSE_NEGATION.search(clean):
        return False
    has_https_link = bool(re.search(r"(?i)https://[^\s<>()\"']+", clean))
    has_platform = bool(_COURSE_PLATFORM.search(clean))
    return bool((has_platform or has_https_link) and _COURSE_ACTION.search(clean))


def is_saved_course_platform_followup(text: str, bookmark: object) -> bool:
    """Continue a course task in its already selected browser when the user asks for the next step."""
    saved = clean_app_context(bookmark)
    if not saved or saved["app"] not in _BROWSER_APP_KEYS:
        return False
    if not re.search(r"(?i)\b(canvas|pearson|mcgraw\s*-?\s*hill|smart\s*book|connect|course|assignment|homework)\b", saved["task"]):
        return False
    return is_course_platform_request(text) or bool(re.search(
        r"(?i)^\s*(?:do|answer|complete|finish|work through|continue|review)\b.{0,50}\b(?:next|another|remaining|assignment|homework|question|problem|chapter)\b",
        _unquoted_user_text(text),
    ))

def clean_app_context(value):
    if not isinstance(value, dict) or value.get('app') not in APP_NAMES:
        return None
    app = value['app']
    def short(key, limit):
        item=value.get(key,'')
        return item[:limit] if isinstance(item,str) else ''
    status=value.get('status','ready')
    steps=value.get('completed_steps',[])
    return {'app':app,'name':APP_NAMES[app],'task':short('task',6000),
            'status':status if status in ('ready','working','waiting','done','stopped') else 'ready',
            'last_result':short('last_result',1500),
            'completed_steps':[step[:350] for step in steps[-12:] if isinstance(step,str)] if isinstance(steps,list) else []}

def is_app_followup(text, bookmark):
    bookmark = clean_app_context(bookmark)
    if not bookmark or not isinstance(text,str): return False
    text=text.strip().lower()
    if is_permission_recovery(text, bookmark): return True
    if re.match(r"^(how|why|what|when|who|explain|describe|compare|don't|do not|never|avoid)\b",text): return False
    text=re.sub(r'^(?:(?:okay|ok|now|then|and|please)\b[\s,]*)+','',text)
    text=re.sub(r'^(?:(?:can|could|would) you|i (?:want|need) you to)\s+','',text)
    return bool(re.match(r'(?:open|launch|go|navigate|find|search|look|check|read|scroll|click|select|type|press|send|say|message|reply|tell|resume|continue|finish|keep going|do (?:it|that|this))\b',text))


def is_permission_recovery(text, bookmark):
    """Route a narrow permission acknowledgment back to its stopped app task.

    This is routing only. It never records approval or changes per-action policy.
    The automation backend must ask again for the concrete task/action.
    """
    saved = clean_app_context(bookmark)
    if not saved or saved['status'] != 'stopped' or not is_permission_recovery_phrase(text):
        return False
    failure = saved['last_result'].lower()
    if not any(marker in failure for marker in ('not approved', 'permission', 'still lacks', 'action declined')):
        return False
    return True


def is_permission_recovery_phrase(text):
    return isinstance(text, str) and len(text) <= 240 and bool(_APPROVAL_RECOVERY.fullmatch(text.strip()))
