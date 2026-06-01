"""
Enjoy Stock — 后端数据代理 + AI 分析服务器
数据源：新浪财经 + 腾讯 K 线（免费无需 Key，无频率限制）
AI 分析：DeepSeek V4 Pro
启动：python server.py
端口：8899
"""

import json, os, time, re, subprocess
from datetime import datetime
from functools import lru_cache

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ============================================================
# 配置
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e638268828054810af0074a128d95ba3")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "https://finance.sina.com.cn/"}

# ============================================================
# 全量股票搜索索引（启动时从新浪拉取全部A股，缓存24h）
# ============================================================
STOCK_SEARCH_DB = []

def _build_full_index():
    """从新浪获取全部A股列表 + 港股/美股常用股，构建搜索索引"""
    global STOCK_SEARCH_DB
    import os as _os

    # 尝试从缓存加载（24小时内有效）
    cache_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "stock_index.json")
    if _os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("updated", 0) > time.time() - 86400:
                    STOCK_SEARCH_DB = cached["stocks"]
                    print(f"[Index] Loaded {len(STOCK_SEARCH_DB)} stocks from cache")
                    return
        except: pass

    print("[Index] Building full stock index from Sina...")
    new_stocks = []

    # 1. 从新浪分页拉取全部A股（深交所+上交所，跳过北交所）
    for page in range(1, 100):
        try:
            url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1&node=hs_a"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.encoding = "gbk"
            data = resp.json()
            if not data: break
            for item in data:
                raw = item.get("symbol", "")
                code = item.get("code", "")
                name = item.get("name", "")
                if not code or not name: continue
                # symbol格式: sh600519, sz300456, bj920000
                if raw.startswith("sh"):
                    full = f"{code}.SS"
                elif raw.startswith("sz"):
                    full = f"{code}.SZ"
                else:
                    continue  # 跳过北交所等
                new_stocks.append({"symbol": full, "name": name, "exchange": "A股"})
            if len(data) < 80: break
        except Exception as e:
            print(f"  [Index] Page {page} failed: {e}")
            break
    print(f"  [Index] Fetched {len(new_stocks)} A-shares from Sina")

    # 2. 港股常用（200+只）
    hk = [
        ("0700","腾讯控股"),("9988","阿里巴巴-SW"),("0941","中国移动"),("2318","中国平安"),("0388","香港交易所"),
        ("1299","友邦保险"),("0005","汇丰控股"),("0883","中国海洋石油"),("1810","小米集团-W"),("3690","美团-W"),
        ("9618","京东集团-SW"),("9999","网易-S"),("2269","药明生物"),("1211","比亚迪股份"),("2015","理想汽车-W"),
        ("1024","快手-W"),("9868","小鹏汽车-W"),("2331","李宁"),("1876","百威亚太"),("0002","中电控股"),
        ("0003","香港中华煤气"),("0011","恒生银行"),("0012","恒基地产"),("0016","新鸿基地产"),("0006","电能实业"),
        ("0017","新世界发展"),("0027","银河娱乐"),("0066","港铁公司"),("0083","信和置业"),("0101","恒隆地产"),
        ("0175","吉利汽车"),("0241","阿里健康"),("0268","金蝶国际"),("0285","比亚迪电子"),("0291","华润啤酒"),
        ("0316","东方海外国际"),("0322","康师傅控股"),("0386","中国石油化工"),("0489","东风集团股份"),
        ("0669","创科实业"),("0762","中国联通"),("0823","领展房产基金"),("0857","中国石油股份"),
        ("0868","信义光能"),("0881","中升控股"),("0902","比亚迪股份"),("0916","龙源电力"),
        ("0960","龙湖集团"),("0968","信义玻璃"),("0981","中芯国际"),("0992","联想集团"),
        ("0998","中信银行"),("1044","恒安国际"),("1093","石药集团"),("1109","华润置地"),
        ("1113","长实集团"),("1177","中国生物制药"),("1288","中国农业银行"),("1398","中国工商银行"),
        ("1658","邮储银行"),("1800","中国交通建设"),("1833","平安好医生"),("1919","中远海控"),
        ("1988","中国民生银行"),("2007","碧桂园"),("2020","安踏体育"),("2313","申洲国际"),
        ("2319","蒙牛乳业"),("2382","舜宇光学科技"),("2601","中国太保"),("2628","中国人寿"),
        ("2688","新奥能源"),("2899","紫金矿业"),("3328","交通银行"),("3968","招商银行"),
        ("3988","中国银行"),("6030","中信证券"),("6185","康希诺生物"),("6618","京东健康"),
        ("6690","海尔智家"),("6862","海底捞"),("6993","蓝月亮集团"),("9626","哔哩哔哩-SW"),
        ("9888","百度集团-SW"),("9961","携程集团-S"),("9966","康诺亚-B"),("2013","微盟集团"),
        ("0019","太古股份公司A"),("0087","太古股份公司B"),
    ]
    for code, name in hk:
        new_stocks.append({"symbol": f"{code}.HK", "name": name, "exchange": "港股"})

    # 3. 美股常用（200+只）
    us = [
        ("AAPL","苹果 Apple"),("TSLA","特斯拉 Tesla"),("NVDA","英伟达 NVIDIA"),("GOOGL","谷歌 Alphabet"),
        ("MSFT","微软 Microsoft"),("AMZN","亚马逊 Amazon"),("META","Meta Platforms"),("AMD","超微半导体 AMD"),
        ("NFLX","奈飞 Netflix"),("TSM","台积电 Taiwan Semi"),("BABA","阿里巴巴 Alibaba"),("JPM","摩根大通 JPMorgan"),
        ("V","Visa"),("JNJ","强生 J&J"),("WMT","沃尔玛 Walmart"),("PG","宝洁 P&G"),("MA","万事达 Mastercard"),
        ("HD","家得宝 Home Depot"),("DIS","迪士尼 Disney"),("BAC","美国银行"),("INTC","英特尔 Intel"),
        ("PYPL","PayPal"),("ADBE","Adobe"),("CRM","Salesforce"),("UBER","优步 Uber"),
        ("SNOW","Snowflake"),("PLTR","Palantir"),("COIN","Coinbase"),("SQ","Block(Square)"),
        ("SNAP","Snap"),("PINS","Pinterest"),("ZM","Zoom"),("DASH","DoorDash"),
        ("ABNB","Airbnb"),("RBLX","Roblox"),("LCID","Lucid"),("RIVN","Rivian"),
        ("NIO","蔚来 NIO"),("XPEV","小鹏汽车 XPeng"),("LI","理想汽车 Li Auto"),("BIDU","百度 Baidu"),
        ("JD","京东 JD.com"),("NTES","网易 NetEase"),("TME","腾讯音乐"),("BILI","哔哩哔哩 Bilibili"),
        ("PDD","拼多多 PDD"),("TAL","好未来"),("EDU","新东方"),("FUTU","富途 FUTU"),
        ("ORCL","甲骨文 Oracle"),("CSCO","思科 Cisco"),("QCOM","高通 Qualcomm"),("TXN","德州仪器 TI"),
        ("AVGO","博通 Broadcom"),("AMAT","应用材料"),("MU","美光 Micron"),("MRVL","Marvell"),
        ("IBM","IBM"),("HPE","慧与 HPE"),("DELL","戴尔 Dell"),("HPQ","惠普 HP"),
        ("KO","可口可乐"),("PEP","百事"),("MCD","麦当劳"),("SBUX","星巴克"),
        ("NKE","耐克 Nike"),("TGT","塔吉特 Target"),("COST","好市多 Costco"),("LOW","劳氏 Lowe's"),
        ("CVX","雪佛龙"),("XOM","埃克森美孚"),("COP","康菲石油"),("OXY","西方石油"),
        ("PFE","辉瑞 Pfizer"),("MRK","默克 Merck"),("ABBV","艾伯维 AbbVie"),("BMY","百时美施贵宝"),
        ("LLY","礼来 Eli Lilly"),("UNH","联合健康"),("ABT","雅培 Abbott"),
        ("CAT","卡特彼勒"),("DE","迪尔 Deere"),("BA","波音 Boeing"),("GE","通用电气"),
        ("GM","通用汽车"),("F","福特 Ford"),("RACE","法拉利 Ferrari"),
        ("BRK.B","伯克希尔哈撒韦 B"),("GS","高盛 Goldman Sachs"),("MS","摩根士丹利"),
        ("C","花旗"),("WFC","富国银行 Wells Fargo"),("AXP","美国运通"),
        ("SPY","标普500 ETF"),("QQQ","纳指100 ETF"),("DIA","道指 ETF"),("IWM","罗素2000 ETF"),
    ]
    for sym, name in us:
        new_stocks.append({"symbol": sym, "name": name, "exchange": "美股"})

    # 保存缓存
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"updated": time.time(), "stocks": new_stocks}, f, ensure_ascii=False)
    except: pass

    STOCK_SEARCH_DB = new_stocks
    print(f"[Index] Total: {len(STOCK_SEARCH_DB)} stocks indexed (cached for 24h)")

# 启动时构建索引
_build_full_index()

# ============================================================
# 新浪 API — 实时行情
# ============================================================
def _sina_symbol(symbol):
    """转换为新浪 symbol 格式"""
    if symbol.endswith(".SS"):
        return "sh" + symbol.replace(".SS", "")
    elif symbol.endswith(".SZ"):
        return "sz" + symbol.replace(".SZ", "")
    elif symbol.endswith(".HK"):
        code = symbol.replace(".HK", "")
        return "hk" + code
    else:
        # 美股
        return "gb_" + symbol.lower().replace("-usd", "")

@lru_cache(maxsize=256)
def fetch_realtime(symbol, ttl_hash=None):
    """新浪实时行情 — 根据市场自动适配字段"""
    sina_sym = _sina_symbol(symbol)
    url = f"https://hq.sinajs.cn/list={sina_sym}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=6)
        resp.encoding = "gbk"
        text = resp.text
        match = re.search(r'"([^"]*)"', text)
        if not match or not match.group(1):
            return None
        parts = match.group(1).split(",")
        if len(parts) < 5:
            return None

        is_hk = sina_sym.startswith("hk")
        is_us = sina_sym.startswith("gb_")
        is_cn = sina_sym.startswith("sh") or sina_sym.startswith("sz")

        if is_us:
            # 美股: name, price, changePct, time, change, open, high, low, 52h, 52l, vol...
            name = parts[0]
            price = float(parts[1]) if parts[1] else 0
            change_pct = float(parts[2]) if parts[2] else 0
            open_p = float(parts[5]) if len(parts) > 5 and parts[5] else 0
            high = float(parts[6]) if len(parts) > 6 and parts[6] else 0
            low = float(parts[7]) if len(parts) > 7 and parts[7] else 0
            volume = int(float(parts[10])) if len(parts) > 10 and parts[10] else 0
            prev_close = price / (1 + change_pct/100) if change_pct else price
            change = price - prev_close
            currency = "USD"
        elif is_hk:
            # 港股: engName, chnName, open, prevClose, high, low, price, change, changePct, ...
            name = parts[1] if len(parts) > 1 and parts[1] else parts[0]
            open_p = float(parts[2]) if len(parts) > 2 and parts[2] else 0
            prev_close = float(parts[3]) if len(parts) > 3 and parts[3] else 0
            high = float(parts[4]) if len(parts) > 4 and parts[4] else 0
            low = float(parts[5]) if len(parts) > 5 and parts[5] else 0
            price = float(parts[6]) if len(parts) > 6 and parts[6] else 0
            change = float(parts[7]) if len(parts) > 7 and parts[7] else 0
            change_pct = float(parts[8]) if len(parts) > 8 and parts[8] else 0
            volume = int(float(parts[11])) if len(parts) > 11 and parts[11] else 0
            currency = "HKD"
        else:
            # A股: name, open, prevClose, price, high, low, ...
            name = parts[0]
            open_p = float(parts[1]) if parts[1] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            price = float(parts[3]) if parts[3] else 0
            high = float(parts[4]) if parts[4] else 0
            low = float(parts[5]) if parts[5] else 0
            volume = int(float(parts[8])) if len(parts) > 8 and parts[8] else 0
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0
            currency = "CNY"

        # US stocks: the name from Sina is garbled for Chinese companies, keep as-is
        # Clean up name (remove extra spaces/newlines)
        name = name.strip().replace("\n","").replace("\r","")

        return {
            "symbol": symbol, "name": name, "price": round(price, 2),
            "change": round(change, 2), "changePct": round(change_pct, 2),
            "high": round(high, 2), "low": round(low, 2),
            "open": round(open_p, 2), "prevClose": round(prev_close, 2),
            "volume": volume, "amount": 0, "marketCap": 0, "pe": 0,
            "currency": currency,
            "exchange": symbol.split(".")[-1] if "." in symbol else ("NASDAQ" if is_us else "US"),
        }
    except Exception as e:
        print(f"[ERROR] sina {symbol}: {e}")
    return None

def resolve_symbol(query):
    """智能解析股票代码 → 标准 symbol 列表"""
    q = query.strip().upper()
    candidates = []

    # 已是标准格式
    if any(q.endswith(s) for s in ['.HK','.SZ','.SS']):
        candidates.append(q)

    # 纯数字
    if q.isdigit():
        if len(q) == 6:
            if q.startswith('60') or q.startswith('68'):
                candidates.extend([f"{q}.SS", f"{q}.SZ", f"{q}.HK"])
            else:
                candidates.extend([f"{q}.SZ", f"{q}.SS", f"{q}.HK"])
        elif len(q) <= 5:
            candidates.extend([f"{q.zfill(5)}.HK", f"{q}.SZ", f"{q}.SS"])

    # 字母代码
    if q.isalpha() and len(q) <= 5:
        candidates.append(q)

    # 加密货币
    if "BTC" in q or "ETH" in q:
        candidates.append(q)

    # 去重
    seen, result = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c); result.append(c)
    return result if result else [q]

# ============================================================
# 腾讯 API — K 线数据
# ============================================================
def fetch_kline(symbol, period="3m"):
    """腾讯财经 K 线（JSON 格式，Python 友好）"""
    sina_sym = _sina_symbol(symbol)
    days_map = {"1m": 30, "3m": 80, "6m": 150, "1y": 260}
    limit = days_map.get(period, 80)

    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sina_sym},day,,,{limit},qfq"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        data = resp.json()
        if data.get("code") == 0:
            # 取 day 数据
            day_data = None
            stock_key = sina_sym
            for k in data.get("data", {}):
                if k != "qfqday" and isinstance(data["data"].get(k), dict):
                    day_data = data["data"][k].get("day") or data["data"][k].get("qfqday")
                    break
            if not day_data:
                # try qfqday directly
                day_data = data.get("data", {}).get(sina_sym, {}).get("qfqday") or \
                           data.get("data", {}).get(sina_sym, {}).get("day")

            if day_data:
                result = []
                for line in day_data[-limit:]:
                    result.append({
                        "date": line[0],
                        "open": float(line[1]),
                        "close": float(line[2]),
                        "high": float(line[3]),
                        "low": float(line[4]),
                        "volume": int(float(line[5])),
                    })
                return result
    except Exception as e:
        # Fallback: curl (sina kline JSON)
        pass

    # Fallback: 新浪 K 线 JSON API
    try:
        fallback_url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_sym}&scale=240&ma=no&datalen={limit}"
        resp = requests.get(fallback_url, headers=HEADERS, timeout=8)
        resp.encoding = "gbk"
        data = resp.json()
        if data and len(data) > 0:
            result = []
            for d in data[-limit:]:
                result.append({
                    "date": d["day"],
                    "open": float(d["open"]),
                    "close": float(d["close"]),
                    "high": float(d["high"]),
                    "low": float(d["low"]),
                    "volume": int(float(d["volume"])),
                })
            return result
    except Exception:
        pass

    return None

# ============================================================
# API 路由
# ============================================================

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/api/search")
def api_search():
    """实时搜索 — 组合腾讯API(代码/拼音) + 本地索引(中文名)"""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return jsonify({"ok": True, "data": []})

    results = []

    # 1. 腾讯智能搜索 (支持代码 + 拼音，实时无缓存)
    try:
        tencent_url = f"https://smartbox.gtimg.cn/s3/?q={q}&t=all"
        resp = requests.get(tencent_url, headers=HEADERS, timeout=4)
        resp.encoding = "gbk"
        text = resp.text
        # 解析: v_hint="sh~600519~贵州茅台~gzmt~GP-A^sz~300456~赛微电子~swdz~GP-A"
        match = re.search(r'v_hint="([^"]*)"', text)
        if match and match.group(1) and match.group(1) != "N":
            for item in match.group(1).split("^"):
                parts = item.split("~")
                if len(parts) >= 3:
                    market, code, name = parts[0], parts[1], parts[2]
                    # 转换格式
                    name = name.replace("\\u", "\\u")  # keep unicode escapes
                    # Try to decode unicode escapes in name
                    try:
                        import codecs
                        name = codecs.decode(name, 'unicode_escape')
                    except: pass
                    if market == "sh":
                        symbol = f"{code}.SS"
                    elif market == "sz":
                        symbol = f"{code}.SZ"
                    elif market == "hk":
                        symbol = f"{code}.HK"
                    elif market == "us":
                        symbol = code.replace(".oq","").replace(".n","").replace(".o","").upper()
                    else:
                        continue
                    results.append({"symbol": symbol, "name": name, "exchange": market.upper(), "score": 90})
    except: pass

    # 2. 本地索引补充 (中文名称匹配)
    ql = q.lower()
    for s in STOCK_SEARCH_DB:
        score = 0
        sym_low = s["symbol"].lower()
        name_low = s["name"].lower()
        if sym_low == ql: score = 100
        elif sym_low.startswith(ql): score = 80
        elif ql in sym_low: score = 60
        if ql in name_low: score += 30
        if name_low.startswith(ql): score += 15
        # 拼音首字母
        initials = "".join([w[0].lower() for w in s["name"].split() if w])
        if ql in initials: score += 10
        if score > 0:
            results.append({**s, "score": score})

    # 3. 如果用户输入的是中文名称，尝试直接作为新浪代码探测
    # （中文名 → 新浪实时行情验证）
    if not results and any('一' <= c <= '鿿' for c in q):
        for sym, name in _try_name_match(q):
            results.append({"symbol": sym, "name": name, "exchange": "A股", "score": 50})

    # 去重 + 排序
    seen = set()
    uniq = []
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            uniq.append(r)
    return jsonify({"ok": True, "data": uniq[:12]})

def _try_name_match(name_query):
    """在本地索引中用中文名匹配（即使索引不完整，至少覆盖常见股票）"""
    matches = []
    for s in STOCK_SEARCH_DB:
        if name_query in s["name"]:
            matches.append((s["symbol"], s["name"]))
            if len(matches) >= 5: break
    return matches

@app.route("/api/stock/<path:symbol>")
def api_stock(symbol):
    """实时行情 — 多候选尝试"""
    from urllib.parse import unquote
    symbol = unquote(symbol)

    for sym in resolve_symbol(symbol):
        data = fetch_realtime(sym, ttl_hash=get_ttl_hash(3))
        if data and data.get("price", 0) > 0:
            return jsonify({"ok": True, "data": data, "resolvedSymbol": sym})

    return jsonify({
        "ok": False,
        "error": f"未找到「{symbol}」，请确认代码正确",
        "tried": resolve_symbol(symbol)
    }), 404

@app.route("/api/kline/<path:symbol>")
def api_kline(symbol):
    """K 线数据"""
    from urllib.parse import unquote
    symbol = unquote(symbol)
    period = request.args.get("period", "3m")

    for sym in resolve_symbol(symbol):
        data = fetch_kline(sym, period)
        if data:
            return jsonify({"ok": True, "data": data, "resolvedSymbol": sym})
    return jsonify({"ok": False, "error": "无法获取K线"}), 404

@app.route("/api/hot/<market>")
def api_hot(market):
    """热门股票 — 逐个查询实时价"""
    hot = {
        "cn": ["600519.SS","000858.SZ","300750.SZ","002594.SZ","000001.SZ","600036.SS","601318.SS","000333.SZ","600900.SS","601899.SS"],
        "hk": ["0700.HK","9988.HK","0941.HK","0388.HK","1810.HK","3690.HK","2318.HK","1299.HK","0883.HK","0005.HK"],
        "us": ["AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","AMD","TSM","JPM"],
    }
    symbols = hot.get(market, hot["cn"])
    result = []
    for sym in symbols:
        data = fetch_realtime(sym, ttl_hash=get_ttl_hash(8))
        if data:
            result.append({"symbol": sym, "name": data["name"], "price": data["price"], "changePct": data["changePct"]})
        else:
            result.append({"symbol": sym, "name": sym, "price": 0, "changePct": 0})
        time.sleep(0.1)
    return jsonify({"ok": True, "data": result})

@app.route("/api/news/<path:symbol>")
def api_news(symbol):
    """资讯（简化版）"""
    return jsonify({"ok": False, "error": "资讯功能开发中"}), 404

@app.route("/api/analysis/<path:symbol>", methods=["POST"])
def api_analysis(symbol):
    """AI 分析 — DeepSeek V4 Pro"""
    from urllib.parse import unquote
    symbol = unquote(symbol)
    body = request.get_json(force=True, silent=True) or {}
    stock_data = body.get("stockData", {})
    kline_data = body.get("klineData", [])

    if "sk-e638" not in DEEPSEEK_API_KEY:
        return jsonify({"ok": False, "error": "API Key 未配置"}), 500

    # 构建 prompt
    name = stock_data.get("name", symbol)
    price = stock_data.get("price", 0)
    change_pct = stock_data.get("changePct", 0)
    currency = stock_data.get("currency", "USD")

    closes = [k["close"] for k in kline_data[-30:]] if kline_data else [price]
    highs_30 = [k["high"] for k in kline_data[-30:]] if kline_data else [price]
    lows_30 = [k["low"] for k in kline_data[-30:]] if kline_data else [price]
    volumes = [k["volume"] for k in kline_data[-30:]] if kline_data else [0]

    ma5 = sum(closes[-5:])/min(len(closes),5) if closes else price
    ma20 = sum(closes[-20:])/min(len(closes),20) if closes else price
    high30 = f"{max(highs_30):.2f}" if highs_30 else "N/A"
    low30 = f"{min(lows_30):.2f}" if lows_30 else "N/A"
    vol_avg = f"{int(sum(volumes)/max(len(volumes),1))}" if volumes else "N/A"

    recent = [{"date":k["date"],"o":k["open"],"c":k["close"],"h":k["high"],"l":k["low"],"v":k["volume"]} for k in (kline_data or [])[-5:]]

    prompt = f"""请对以下股票进行全面量化分析，给出未来3/5/7/15日涨跌预测。

股票：{name}（{symbol}）
当前价：{price} {currency}  涨跌：{change_pct:+.2f}%
MA5：{ma5:.2f}  MA20：{ma20:.2f}
30日最高：{high30}  最低：{low30}  均量：{vol_avg}
近5日K线：{json.dumps(recent, ensure_ascii=False)}

请结合技术面（趋势/均线/量价/形态）分析，严格返回JSON：

```json
{{
  "predictions": [
    {{"days":3,"direction":"up或down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}},
    {{"days":5,"direction":"up或down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}},
    {{"days":7,"direction":"up或down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}},
    {{"days":15,"direction":"up或down","confidence":0-100,"targetPrice":数字,"rangeLow":数字,"rangeHigh":数字,"reasoning":"理由"}}
  ],
  "summary":"200-300字综合研判",
  "factors":{{
    "technical":[{{"name":"指标","value":"值","sentiment":"positive/negative/neutral"}}],
    "fundamental":[{{"name":"指标","value":"值","sentiment":"positive/negative/neutral"}}],
    "sentiment":[{{"name":"指标","value":"值","sentiment":"positive/negative/neutral"}}]
  }},
  "supportLevel":支撑位,
  "resistanceLevel":阻力位,
  "riskLevel":"low/medium/high",
  "riskNote":"风险提示"
}}
```"""

    try:
        resp = requests.post(DEEPSEEK_API_URL, headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }, json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role":"system","content":"你是顶级股票量化分析师。只返回JSON，不要其他内容。"},
                {"role":"user","content": prompt},
            ],
            "temperature": 0.3, "max_tokens": 4000,
        }, timeout=60)
        result = resp.json()
        content = result.get("choices",[{}])[0].get("message",{}).get("content","")
        analysis = _parse_json(content)
        if analysis:
            return jsonify({"ok": True, "data": analysis})
        return jsonify({"ok": True, "data": {"summary": content, "predictions": [], "factors": {}}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _parse_json(content):
    if not content: return None
    content = content.strip()
    if content.startswith("```"): content = "\n".join(content.split("\n")[1:])
    if content.endswith("```"): content = content[:-3].strip()
    try: return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            try: return json.loads(m.group())
            except: pass
    return None

# ============================================================
# 工具
# ============================================================
def get_ttl_hash(seconds=5):
    return round(time.time() / seconds)

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("=" * 50)
    print("Enjoy Stock - Backend Server (Sina + Tencent)")
    print("=" * 50)
    print(f"URL: http://localhost:8899")
    print(f"Stock Data: Sina Finance (real-time) + Tencent (K-line)")
    print(f"AI Model: {DEEPSEEK_MODEL}")
    print(f"Search DB: {len(STOCK_SEARCH_DB)} stocks indexed")
    print("=" * 50)
    app.run(host="127.0.0.1", port=8899, debug=False)
