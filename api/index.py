import json, os, re
from urllib.parse import unquote, urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

# Lazy import to avoid cold-start crashes
def get_requests():
    import requests
    return requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e638268828054810af0074a128d95ba3")

# Compact stock DB (built at import — fast, no API calls)
COMPACT = []
def _build_compact():
    global COMPACT
    cn = [("600519.SS","贵州茅台"),("000858.SZ","五粮液"),("300750.SZ","宁德时代"),("002594.SZ","比亚迪"),("000001.SZ","平安银行"),("600036.SS","招商银行"),("601318.SS","中国平安"),("000333.SZ","美的集团"),("600900.SS","长江电力"),("601899.SS","紫金矿业"),("600276.SS","恒瑞医药"),("000568.SZ","泸州老窖"),("002415.SZ","海康威视"),("000651.SZ","格力电器"),("002475.SZ","立讯精密"),("300059.SZ","东方财富"),("000725.SZ","京东方A"),("600030.SS","中信证券"),("600887.SS","伊利股份"),("300124.SZ","汇川技术"),("000063.SZ","中兴通讯"),("002230.SZ","科大讯飞"),("002352.SZ","顺丰控股"),("000002.SZ","万科A"),("300015.SZ","爱尔眼科"),("600809.SS","山西汾酒"),("601398.SS","工商银行"),("601857.SS","中国石油"),("600028.SS","中国石化"),("000538.SZ","云南白药")]
    hk = [("0700.HK","腾讯控股"),("9988.HK","阿里巴巴-SW"),("0941.HK","中国移动"),("2318.HK","中国平安"),("0388.HK","香港交易所"),("1299.HK","友邦保险"),("0005.HK","汇丰控股"),("0883.HK","中国海洋石油"),("1810.HK","小米集团-W"),("3690.HK","美团-W")]
    us = [("AAPL","苹果 Apple"),("TSLA","特斯拉 Tesla"),("NVDA","英伟达 NVIDIA"),("GOOGL","谷歌 Alphabet"),("MSFT","微软 Microsoft"),("AMZN","亚马逊 Amazon"),("META","Meta"),("AMD","AMD"),("NFLX","奈飞"),("TSM","台积电")]
    for s,n in cn: COMPACT.append({"symbol":s,"name":n,"exchange":"A股"})
    for s,n in hk: COMPACT.append({"symbol":s,"name":n,"exchange":"港股"})
    for s,n in us: COMPACT.append({"symbol":s,"name":n,"exchange":"美股"})
_build_compact()

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            path = unquote(self.path)
            if path == "/api/health":
                self._json({"status":"ok"})
            elif path.startswith("/api/search"):
                self._search(path)
            elif path.startswith("/api/stock/"):
                self._stock(path)
            elif path.startswith("/api/kline/"):
                self._kline(path)
            elif path.startswith("/api/hot/"):
                self._hot(path)
            else:
                self._json({"error":"not found"}, 404)
        except Exception as e:
            self._json({"error":str(e)}, 500)

    def do_POST(self):
        try:
            path = unquote(self.path)
            if path.startswith("/api/analysis/"):
                self._analysis(path)
            else:
                self._json({"error":"not found"}, 404)
        except Exception as e:
            self._json({"error":str(e)}, 500)

    def do_OPTIONS(self):
        self._cors(200)

    def _cors(self, code=200):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    # ---- Search ----
    def _search(self, path):
        q = parse_qs(urlparse(path).query).get("q",[""])[0].strip()
        if not q: return self._json({"ok":True,"data":[]})
        results = []
        try:
            r = get_requests().get(f"https://smartbox.gtimg.cn/s3/?q={q}&t=all", headers=HEADERS, timeout=4)
            r.encoding="gbk"; m=re.search(r'v_hint="([^"]*)"',r.text)
            if m and m.group(1)!="N":
                for item in m.group(1).split("^"):
                    p=item.split("~")
                    if len(p)>=3:
                        mk,code,name=p[0],p[1],p[2]
                        try: import codecs; name=codecs.decode(name,'unicode_escape')
                        except: pass
                        sym=f"{code}.SS" if mk=="sh" else f"{code}.SZ" if mk=="sz" else f"{code}.HK" if mk=="hk" else code.replace(".oq","").upper() if mk=="us" else None
                        if sym: results.append({"symbol":sym,"name":name,"exchange":mk.upper(),"score":90})
        except: pass
        ql=q.lower()
        for s in COMPACT:
            sc=0
            if ql in s["name"].lower(): sc+=30
            if s["symbol"].lower().startswith(ql): sc+=80
            elif ql in s["symbol"].lower(): sc+=60
            if sc>0: results.append({**s,"score":sc})
        seen=set(); uniq=[]
        for r2 in sorted(results,key=lambda x:x["score"],reverse=True):
            if r2["symbol"] not in seen: seen.add(r2["symbol"]); uniq.append(r2)
        self._json({"ok":True,"data":uniq[:12]})

    # ---- Stock Quote ----
    def _stock(self, path):
        symbol = path.split("/api/stock/")[-1]
        d = self._fetch_quote(symbol)
        if d: return self._json({"ok":True,"data":d})
        self._json({"ok":False,"error":f"未找到「{symbol}」"},404)

    def _fetch_quote(self, symbol):
        s = symbol.upper()
        if s.endswith(".HK"): sina="hk"+s.replace(".HK","")
        elif s.endswith(".SZ"): sina="sz"+s.replace(".SZ","")
        elif s.endswith(".SS"): sina="sh"+s.replace(".SS","")
        else: sina="gb_"+s.lower()
        try:
            r=get_requests().get(f"https://hq.sinajs.cn/list={sina}",headers=HEADERS,timeout=6)
            r.encoding="gbk"; m=re.search(r'"([^"]*)"',r.text)
            if not m: return None
            p=m.group(1).split(",")
            if len(p)<5: return None
            is_us=sina.startswith("gb_"); is_hk=sina.startswith("hk")
            if is_us:
                nm,pr=p[0],float(p[1]or 0); cp=float(p[2]or 0)
                op,hi,lo=float(p[5]or 0),float(p[6]or 0),float(p[7]or 0)
                vl=int(float(p[10]or 0)); pv=pr/(1+cp/100) if cp else pr; cu="USD"
            elif is_hk:
                nm=p[1]or p[0]; op,prv=float(p[2]or 0),float(p[3]or 0); hi,lo=float(p[4]or 0),float(p[5]or 0)
                pr=float(p[6]or 0); ch=float(p[7]or 0); cp=float(p[8]or 0); vl=int(float(p[11]or 0)); cu="HKD"
            else:
                nm=p[0]; op,prv=float(p[1]or 0),float(p[2]or 0); pr=float(p[3]or 0); hi,lo=float(p[4]or 0),float(p[5]or 0)
                vl=int(float(p[8]or 0)); ch=pr-prv if prv else 0; cp=(ch/prv*100)if prv else 0; cu="CNY"
            return {"symbol":s,"name":nm.strip(),"price":round(pr,2),"change":round(ch,2) if 'ch' in dir() else round(pr-pv,2),
                    "changePct":round(cp,2),"high":round(hi,2),"low":round(lo,2),"open":round(op,2),
                    "prevClose":round(pv,2) if 'pv' in dir() else round(prv,2),"volume":vl,"currency":cu}
        except: return None

    # ---- K-line ----
    def _kline(self, path):
        symbol = path.split("/api/kline/")[-1].split("?")[0]
        period = parse_qs(urlparse(path).query).get("period",["3m"])[0]
        days = {"1m":30,"3m":80,"6m":150,"1y":260}.get(period,80)
        s = symbol.upper()
        if s.endswith(".SZ"): sina="sz"+s.replace(".SZ","")
        elif s.endswith(".SS"): sina="sh"+s.replace(".SS","")
        elif s.endswith(".HK"): sina="hk"+s.replace(".HK","")
        else: sina="gb_"+s.lower()
        try:
            r=get_requests().get(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sina},day,,,{days},qfq",headers=HEADERS,timeout=8)
            d=r.json()
            if d.get("code")==0:
                for k in d.get("data",{}):
                    if k!="qfqday" and isinstance(d["data"].get(k),dict):
                        day=d["data"][k].get("day") or d["data"][k].get("qfqday")
                        if day:
                            return self._json({"ok":True,"data":[{"date":l[0],"open":float(l[1]),"close":float(l[2]),"high":float(l[3]),"low":float(l[4]),"volume":int(float(l[5]))} for l in day[-days:]]})
        except: pass
        self._json({"ok":False,"error":"无法获取K线"},404)

    # ---- Hot Stocks ----
    def _hot(self, path):
        market = path.split("/api/hot/")[-1]
        m = {"cn":["600519.SS","000858.SZ","300750.SZ","002594.SZ","000001.SZ","600036.SS","601318.SS","000333.SZ","600900.SS","601899.SS"],"hk":["0700.HK","9988.HK","0941.HK","0388.HK","1810.HK","3690.HK","2318.HK","1299.HK","0883.HK","0005.HK"],"us":["AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","AMD","TSM","JPM"]}
        syms=m.get(market,m["cn"])
        result=[]
        for s in syms:
            d=self._fetch_quote(s)
            if d: result.append({"symbol":s,"name":d["name"],"price":d["price"],"changePct":d["changePct"]})
            else: result.append({"symbol":s,"name":s,"price":0,"changePct":0})
        self._json({"ok":True,"data":result})

    # ---- AI Analysis ----
    def _analysis(self, path):
        symbol = path.split("/api/analysis/")[-1]
        cl = int(self.headers.get("Content-Length",0))
        body=json.loads(self.rfile.read(cl)) if cl else {}
        sd,kd=body.get("stockData",{}),body.get("klineData",[])
        nm,pr=sd.get("name",symbol),sd.get("price",0); cp=sd.get("changePct",0); cu=sd.get("currency","USD")
        cls=[k["close"] for k in kd[-30:]] if kd else [pr]
        ma5=sum(cls[-5:])/min(len(cls),5) if cls else pr
        ma20=sum(cls[-20:])/min(len(cls),20) if cls else pr
        prompt=f"""分析股票{nm}({symbol}) 现价{pr}{cu} 涨跌{cp:+.2f}% MA5:{ma5:.2f} MA20:{ma20:.2f}
近5日K线:{json.dumps([{"d":k["date"],"o":k["open"],"c":k["close"]} for k in (kd or [])[-5:]])}
请给出3/5/7/15日涨跌预测，严格返回JSON:{{"predictions":[{{"days":3,"direction":"up/down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}},...],"summary":"200-300字","factors":{{"technical":[],"fundamental":[],"sentiment":[]}},"supportLevel":数字,"resistanceLevel":数字,"riskLevel":"low/medium/high","riskNote":"风险提示"}}"""
        try:
            r=get_requests().post("https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization":f"Bearer {DEEPSEEK_KEY}","Content-Type":"application/json"},
                json={"model":"deepseek-chat","messages":[{"role":"system","content":"你是股票分析师，只返回JSON。"},{"role":"user","content":prompt}],"temperature":0.3,"max_tokens":4000},timeout=55)
            content=r.json().get("choices",[{}])[0].get("message",{}).get("content","")
            analysis=self._parse_json(content)
            return self._json({"ok":True,"data":analysis or {"summary":content,"predictions":[],"factors":{}}})
        except Exception as e:
            return self._json({"ok":False,"error":str(e)},500)

    def _parse_json(self, content):
        if not content: return None
        c=content.strip()
        if c.startswith("```"): c="\n".join(c.split("\n")[1:])
        if c.endswith("```"): c=c[:-3].strip()
        try: return json.loads(c)
        except:
            m=re.search(r'\{[\s\S]*\}',c)
            if m:
                try: return json.loads(m.group())
                except: pass
        return None
