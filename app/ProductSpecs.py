#!/usr/bin/env python3
"""Read a user-supplied public product page; return visible text for local analysis."""
import html.parser,ipaddress,json,socket,sys,urllib.parse,urllib.request
class Text(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.hidden=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'):self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(data.strip())
def check(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Use a public HTTPS product page')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('Only public product websites are supported')
    return url
class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,url):return super().redirect_request(req,fp,code,msg,headers,check(url))
def run(url):
    opener=urllib.request.build_opener(Redirect())
    with opener.open(urllib.request.Request(check(url),headers={'User-Agent':'WorkDesk-ProductLookup/1.0'}),timeout=25) as response:
        if 'html' not in response.headers.get('Content-Type',''):raise ValueError('Use an HTML product page')
        data=response.read(2_000_001)
        if len(data)>2_000_000:raise ValueError('Product page is too large; attach its specification sheet instead')
        p=Text();p.feed(data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace'))
        text='\n'.join(p.parts)
        # Include nearby specifications preferentially while keeping the page context.
        lines=text.splitlines();matches=[i for i,line in enumerate(lines) if any(w in line.lower() for w in ('dimension','height','width','depth','length','weight',' mm',' cm','inch'))]
        chosen=set(range(min(30,len(lines))))
        for i in matches:
            chosen.update(range(max(0,i-2),min(len(lines),i+5)))
        return {'url':response.url,'text':'\n'.join(lines[i] for i in sorted(chosen))[:14000] or text[:14000]}
if __name__=='__main__':
    try:print(json.dumps(run(json.load(sys.stdin)['url'])))
    except Exception as e:print(json.dumps({'error':str(e)}));sys.exit(1)
