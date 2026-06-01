"""
Enjoy Stock — Vercel Serverless API
数据：新浪实时行情 + 腾讯K线 + DeepSeek AI
"""

import json, os, time, re
from datetime import datetime
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e638268828054810af0074a128d95ba3")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

# 精简常用股票索引（约300只A股+港股+美股，用于中文名搜索）
COMPACT_DB = []
def _init_compact():
    global COMPACT_DB
    stocks = []
    # A股常用300只
    cn_codes = [
        ("600519.SS","贵州茅台"),("000858.SZ","五粮液"),("300750.SZ","宁德时代"),("002594.SZ","比亚迪"),
        ("000001.SZ","平安银行"),("600036.SS","招商银行"),("601318.SS","中国平安"),("000333.SZ","美的集团"),
        ("600900.SS","长江电力"),("601899.SS","紫金矿业"),("600276.SS","恒瑞医药"),("600030.SS","中信证券"),
        ("600887.SS","伊利股份"),("601012.SS","隆基绿能"),("600809.SS","山西汾酒"),("601398.SS","工商银行"),
        ("601288.SS","农业银行"),("600585.SS","海螺水泥"),("600031.SS","三一重工"),("600050.SS","中国联通"),
        ("601857.SS","中国石油"),("600028.SS","中国石化"),("601088.SS","中国神华"),("601166.SS","兴业银行"),
        ("600000.SS","浦发银行"),("601668.SS","中国建筑"),("600048.SS","保利发展"),("601390.SS","中国中铁"),
        ("600104.SS","上汽集团"),("600690.SS","海尔智家"),("601888.SS","中国中免"),("600309.SS","万华化学"),
        ("000568.SZ","泸州老窖"),("002415.SZ","海康威视"),("000651.SZ","格力电器"),("002475.SZ","立讯精密"),
        ("300059.SZ","东方财富"),("000725.SZ","京东方A"),("002714.SZ","牧原股份"),("300124.SZ","汇川技术"),
        ("000063.SZ","中兴通讯"),("002230.SZ","科大讯飞"),("300274.SZ","阳光电源"),("000002.SZ","万科A"),
        ("002352.SZ","顺丰控股"),("300015.SZ","爱尔眼科"),("000792.SZ","盐湖股份"),("002142.SZ","宁波银行"),
        ("300498.SZ","温氏股份"),("000538.SZ","云南白药"),("002371.SZ","北方华创"),("000625.SZ","长安汽车"),
        ("002049.SZ","紫光国微"),("300014.SZ","亿纬锂能"),("002129.SZ","中环股份"),("601615.SS","明阳智能"),
        ("300450.SZ","先导智能"),("002460.SZ","赣锋锂业"),("300433.SZ","蓝思科技"),("002241.SZ","歌尔股份"),
        ("600745.SS","闻泰科技"),("603259.SS","药明康德"),("601066.SS","中信建投"),("600837.SS","海通证券"),
        ("600570.SS","恒生电子"),("601628.SS","中国人寿"),("600009.SS","上海机场"),("600115.SS","中国东航"),
        ("600029.SS","南方航空"),("601111.SS","中国国航"),("600018.SS","上港集团"),("601872.SS","招商轮船"),
    ]
    for sym, name in cn_codes:
        stocks.append({"symbol": sym, "name": name, "exchange": "A股"})
    hk = [("0700.HK","腾讯控股"),("9988.HK","阿里巴巴-SW"),("0941.HK","中国移动"),("2318.HK","中国平安"),("0388.HK","香港交易所"),("1299.HK","友邦保险"),("0005.HK","汇丰控股"),("0883.HK","中国海洋石油"),("1810.HK","小米集团-W"),("3690.HK","美团-W"),("9618.HK","京东集团-SW"),("1211.HK","比亚迪股份"),("2015.HK","理想汽车-W"),("1024.HK","快手-W"),("9868.HK","小鹏汽车-W"),("2269.HK","药明生物"),("2331.HK","李宁"),("2020.HK","安踏体育"),("2319.HK","蒙牛乳业"),("2382.HK","舜宇光学")]
    for sym, name in hk:
        stocks.append({"symbol": sym, "name": name, "exchange": "港股"})
    us = [("AAPL","苹果 Apple"),("TSLA","特斯拉 Tesla"),("NVDA","英伟达 NVIDIA"),("GOOGL","谷歌 Alphabet"),("MSFT","微软 Microsoft"),("AMZN","亚马逊 Amazon"),("META","Meta Platforms"),("AMD","AMD"),("NFLX","奈飞 Netflix"),("TSM","台积电"),("BABA","阿里巴巴"),("JPM","摩根大通"),("BIDU","百度"),("JD","京东"),("NIO","蔚来"),("XPEV","小鹏汽车"),("LI","理想汽车"),("PLTR","Palantir"),("UBER","Uber"),("DIS","迪士尼")]
    for sym, name in us:
        stocks.append({"symbol": sym, "name": name, "exchange": "美股"})
    COMPACT_DB = stocks
_init_compact()

# ============================================================
# 新浪实时行情
# ============================================================
def sina_symbol(symbol):
    if symbol.endswith(".SS"): return "sh" + symbol.replace(".SS", "")
    elif symbol.endswith(".SZ"): return "sz" + symbol.replace(".SZ", "")
    elif symbol.endswith(".HK"): return "hk" + symbol.replace(".HK", "")
    else: return "gb_" + symbol.lower()

def fetch_quote(symbol):
    sym = sina_symbol(symbol)
    try:
        resp = requests.get(f"https://hq.sinajs.cn/list={sym}", headers=HEADERS, timeout=6)
        resp.encoding = "gbk"
        match = re.search(r'"([^"]*)"', resp.text)
        if not match: return None
        parts = match.group(1).split(",")
        if len(parts) < 5: return None
        is_us = sym.startswith("gb_")
        is_hk = sym.startswith("hk")
        if is_us:
            name, price = parts[0], float(parts[1] or 0)
            chg_pct = float(parts[2] or 0)
            opn, hi, lo = float(parts[5] or 0), float(parts[6] or 0), float(parts[7] or 0)
            vol = int(float(parts[10] or 0))
            prev = price/(1+chg_pct/100) if chg_pct else price
            cur = "USD"
        elif is_hk:
            name = parts[1] or parts[0]
            opn, prev = float(parts[2] or 0), float(parts[3] or 0)
            hi, lo = float(parts[4] or 0), float(parts[5] or 0)
            price = float(parts[6] or 0)
            chg, chg_pct = float(parts[7] or 0), float(parts[8] or 0)
            vol = int(float(parts[11] or 0))
            cur = "HKD"
        else:
            name = parts[0]
            opn, prev = float(parts[1] or 0), float(parts[2] or 0)
            price = float(parts[3] or 0)
            hi, lo = float(parts[4] or 0), float(parts[5] or 0)
            vol = int(float(parts[8] or 0))
            chg = price - prev if prev else 0
            chg_pct = (chg/prev*100) if prev else 0
            cur = "CNY"
        return {"symbol":symbol,"name":name.strip(),"price":round(price,2),"change":round(chg,2),"changePct":round(chg_pct,2),"high":round(hi,2),"low":round(lo,2),"open":round(opn,2),"prevClose":round(prev,2),"volume":vol,"currency":cur}
    except: return None

def resolve_symbol(q):
    q = q.strip().upper()
    cand = []
    if any(q.endswith(s) for s in ['.HK','.SZ','.SS']): cand.append(q)
    if q.isdigit():
        if len(q)==6:
            if q.startswith('60') or q.startswith('68'): cand.extend([f"{q}.SS",f"{q}.SZ"])
            else: cand.extend([f"{q}.SZ",f"{q}.SS"])
        elif len(q)<=5: cand.append(f"{q.zfill(5)}.HK")
    if q.isalpha() and len(q)<=5: cand.append(q)
    seen,res=[],set()
    for c in cand:
        if c not in res: res.add(c);seen.append(c)
    return seen if seen else [q]

# ============================================================
# HTTP Handler (Vercel Serverless)
# ============================================================
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path)
        try:
            if path == "/api/health":
                return self.json({"status":"ok"})
            elif path.startswith("/api/search"):
                return self.handle_search(path)
            elif path.startswith("/api/stock/"):
                return self.handle_stock(path)
            elif path.startswith("/api/kline/"):
                return self.handle_kline(path)
            elif path.startswith("/api/hot/"):
                return self.handle_hot(path)
            else:
                return self.json({"error":"not found"}, 404)
        except Exception as e:
            return self.json({"error":str(e)}, 500)

    def do_POST(self):
        path = unquote(self.path)
        try:
            if path.startswith("/api/analysis/"):
                return self.handle_analysis(path)
            return self.json({"error":"not found"}, 404)
        except Exception as e:
            return self.json({"error":str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def handle_search(self, path):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(path).query).get("q",[""])[0].strip()
        if not q: return self.json({"ok":True,"data":[]})
        results = []
        # 腾讯API
        try:
            r = requests.get(f"https://smartbox.gtimg.cn/s3/?q={q}&t=all", headers=HEADERS, timeout=4)
            r.encoding="gbk"; m=re.search(r'v_hint="([^"]*)"',r.text)
            if m and m.group(1)!="N":
                for item in m.group(1).split("^"):
                    p=item.split("~")
                    if len(p)>=3:
                        mkt,code,name=p[0],p[1],p[2]
                        try: import codecs; name=codecs.decode(name,'unicode_escape')
                        except: pass
                        if mkt=="sh": sym=f"{code}.SS"
                        elif mkt=="sz": sym=f"{code}.SZ"
                        elif mkt=="hk": sym=f"{code}.HK"
                        elif mkt=="us": sym=code.replace(".oq","").upper()
                        else: continue
                        results.append({"symbol":sym,"name":name,"exchange":mkt.upper(),"score":90})
        except: pass
        # 本地中文匹配
        ql=q.lower()
        for s in COMPACT_DB:
            sc=0
            if ql in s["name"].lower(): sc+=30
            if s["symbol"].lower().startswith(ql): sc+=80
            elif ql in s["symbol"].lower(): sc+=60
            if sc>0: results.append({**s,"score":sc})
        seen=set(); uniq=[]
        for r2 in sorted(results,key=lambda x:x["score"],reverse=True):
            if r2["symbol"] not in seen: seen.add(r2["symbol"]); uniq.append(r2)
        return self.json({"ok":True,"data":uniq[:12]})

    def handle_stock(self, path):
        symbol = path.split("/api/stock/")[-1]
        for sym in resolve_symbol(symbol):
            d = fetch_quote(sym)
            if d and d.get("price",0)>0:
                return self.json({"ok":True,"data":d,"resolvedSymbol":sym})
        return self.json({"ok":False,"error":f"未找到「{symbol}」"},404)

    def handle_kline(self, path):
        from urllib.parse import urlparse, parse_qs
        symbol = path.split("/api/kline/")[-1].split("?")[0]
        period = parse_qs(urlparse(path).query).get("period",["3m"])[0]
        days = {"1m":30,"3m":80,"6m":150,"1y":260}.get(period,80)
        for sym in resolve_symbol(symbol):
            ss = sina_symbol(sym)
            try:
                r = requests.get(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={ss},day,,,{days},qfq", headers=HEADERS, timeout=8)
                d = r.json()
                if d.get("code")==0:
                    day_data = None
                    for k in d.get("data",{}):
                        if k!="qfqday" and isinstance(d["data"].get(k),dict):
                            day_data = d["data"][k].get("day") or d["data"][k].get("qfqday")
                            break
                    if day_data:
                        result=[{"date":l[0],"open":float(l[1]),"close":float(l[2]),"high":float(l[3]),"low":float(l[4]),"volume":int(float(l[5]))} for l in day_data[-days:]]
                        return self.json({"ok":True,"data":result,"resolvedSymbol":sym})
            except: pass
        return self.json({"ok":False,"error":"无法获取K线"},404)

    def handle_hot(self, path):
        market = path.split("/api/hot/")[-1]
        hot = {"cn":["600519.SS","000858.SZ","300750.SZ","002594.SZ","000001.SZ","600036.SS","601318.SS","000333.SZ","600900.SS","601899.SS"],"hk":["0700.HK","9988.HK","0941.HK","0388.HK","1810.HK","3690.HK","2318.HK","1299.HK","0883.HK","0005.HK"],"us":["AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","AMD","TSM","JPM"]}
        syms = hot.get(market, hot["cn"])
        result=[]
        for s in syms:
            d=fetch_quote(s)
            if d: result.append({"symbol":s,"name":d["name"],"price":d["price"],"changePct":d["changePct"]})
            else: result.append({"symbol":s,"name":s,"price":0,"changePct":0})
        return self.json({"ok":True,"data":result})

    def handle_analysis(self, path):
        symbol = path.split("/api/analysis/")[-1]
        body_len = int(self.headers.get("Content-Length",0))
        body = json.loads(self.rfile.read(body_len)) if body_len else {}
        sd, kd = body.get("stockData",{}), body.get("klineData",[])
        name, price = sd.get("name",symbol), sd.get("price",0)
        cp = sd.get("changePct",0); cur = sd.get("currency","USD")
        cls=[k["close"] for k in kd[-30:]] if kd else [price]
        ma5=sum(cls[-5:])/min(len(cls),5) if cls else price
        ma20=sum(cls[-20:])/min(len(cls),20) if cls else price
        prompt=f"""分析股票{name}({symbol}) 现价{price}{cur} 涨跌{cp:+.2f}% MA5:{ma5:.2f} MA20:{ma20:.2f}
近5日K线:{json.dumps([{"d":k["date"],"o":k["open"],"c":k["close"]} for k in (kd or [])[-5:]])}
请给出3/5/7/15日涨跌预测，严格返回JSON:{{"predictions":[{{"days":3,"direction":"up或down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}},...],"summary":"200-300字","factors":{{"technical":[],"fundamental":[],"sentiment":[]}},"supportLevel":数字,"resistanceLevel":数字,"riskLevel":"low/medium/high","riskNote":"风险"}}"""
        try:
            r=requests.post(DEEPSEEK_API_URL,headers={"Authorization":f"Bearer {DEEPSEEK_API_KEY}","Content-Type":"application/json"},json={"model":"deepseek-chat","messages":[{"role":"system","content":"你是股票分析师，只返回JSON。"},{"role":"user","content":prompt}],"temperature":0.3,"max_tokens":4000},timeout=55)
            content=r.json().get("choices",[{}])[0].get("message",{}).get("content","")
            analysis=_parse_json(content)
            return self.json({"ok":True,"data":analysis or {"summary":content,"predictions":[],"factors":{}}})
        except Exception as e:
            return self.json({"ok":False,"error":str(e)},500)

def _parse_json(content):
    if not content: return None
    content=content.strip()
    if content.startswith("```"): content="\n".join(content.split("\n")[1:])
    if content.endswith("```"): content=content[:-3].strip()
    try: return json.loads(content)
    except:
        m=re.search(r'\{[\s\S]*\}',content)
        if m:
            try: return json.loads(m.group())
            except: pass
    return None
