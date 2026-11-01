# kripto_bot/app/i18n.py
"""Panel languages: English and Turkish.

The panel is written in English, and each English text is its own key: TR maps it to
Turkish. Templates translate with _("text", name=value) and the browser with
KB.t("text", {name: value}) (static/app.js); {name} marks a value filled in at runtime.

Texts the server sends (API errors, setting labels and help, strategy and parameter
descriptions) stay English in the code, so logs, the CLI and the tests
don't change. The browser translates them on arrival: an exact key, or a key with {name}
placeholders used as a template ("no instance named '{name}'"), whose values are looked
up again (so a setting's label inside an error is translated too).

A key may start with "[context] " when one English word has two meanings
("[button] Close" sells a position; "Close" is a candle's close price). The prefix is
never shown.

The language comes from the kb-lang cookie (the switch in the header), else from the
browser's Accept-Language, else English. Telegram messages and console output stay
English.
"""
import re

from markupsafe import Markup

LANGS = ("en", "tr")
COOKIE = "kb-lang"
CONTEXT = re.compile(r"^\[\w+\] ")


def lang_of(request):
    lang = request.cookies.get(COOKIE)
    if lang in LANGS:
        return lang
    return request.accept_languages.best_match(LANGS) or "en"


def catalog(lang):
    """What app.js needs to translate: nothing for English."""
    return TR if lang == "tr" else {}


def translate(text, lang, **values):
    """Text for a template, HTML-escaped; values are escaped too unless they are Markup."""
    s = TR.get(text, text) if lang == "tr" else text
    s = Markup.escape(CONTEXT.sub("", s))
    return s.format(**values) if values else s


TR = {
    # ------------------------------------------------------------ header and shared
    "Overview": "Genel bakış",
    "Instances": "Instance'lar",
    "Playground": "Deneme alanı",
    "Backtests": "Backtest'ler",
    "Settings": "Ayarlar",
    "Language": "Dil",
    "Light / dark": "Açık / koyu",
    "Nothing here yet.": "Henüz bir şey yok.",
    "defaults": "varsayılanlar",
    "Remove from favorites": "Favorilerden çıkar",
    "Add to favorites": "Favorilere ekle",
    "Equal-weight buy & hold of the instance's symbols since its first recorded candle: their average "
    "price change, compounded candle by candle, without fees. It is what the market did over the same period, "
    "so it moves even when the instance holds nothing.":
        "Instance'ın sembollerinin ilk kaydedilen mumdan bu yana eşit ağırlıklı al ve tut getirisi: ortalama "
        "fiyat değişimleri, mum mum bileşik, fee'siz. Aynı dönemde piyasanın ne yaptığını gösterir, bu yüzden "
        "instance hiçbir şey tutmasa da değişir.",
    # setup in words
    "stop {v}": "stop {v}",
    "trail {v}×ATR": "trailing {v}×ATR",
    "{v} sizing": "{v} boyutlama",
    "trend SMA{v}": "trend SMA{v}",
    "lower-TF entry": "alt TF giriş",
    "lower-TF exit": "alt TF çıkış",
    "lower-TF SMA{v}": "alt TF SMA{v}",
    "manual close": "manuel kapatma",
    "{n} symbols": "{n} sembol",
    "{v} USDT per symbol": "sembol başına {v} USDT",
    "{v} USDT capital": "{v} USDT sermaye",
    # instance states (reporting.service_status, run_all)
    "running": "çalışıyor",
    "not running": "çalışmıyor",
    "waiting to restart": "yeniden başlamayı bekliyor",
    "starting": "başlıyor",
    "blocked": "engellendi",
    "stopped": "durduruldu",
    "supervisor not running": "supervisor çalışmıyor",
    "unknown": "bilinmiyor",
    "testnet API keys are missing in .env": ".env'de testnet API anahtarları yok",
    # actions (toasts)
    "start": "başlat",
    "stop": "durdur",
    "restart": "yeniden başlat",
    "{action} requested": "{action} istendi",

    # ------------------------------------------------------------ overview
    "New instance": "Yeni instance",
    "Supervisor": "Supervisor",
    "Kill switch": "Kill switch",
    "testnet / live": "testnet / canlı",
    "Off": "Kapalı",
    "No new entries": "Yeni giriş yok",
    "Block entries": "Girişleri engelle",
    "Close every position": "Bütün pozisyonları kapat",
    "Flatten": "Hepsini kapat",
    "Order execution": "Emir yürütme",
    "Show stopped": "Durdurulanları göster",
    "Sort instances": "Instance'ları sırala",
    "Keeping enabled instances running (pid {pid}).": "Açık instance'ları çalışır tutuyor (pid {pid}).",
    "Start it with {cmd}: it runs every enabled instance and restarts them if they crash.":
        "{cmd} ile başlatın: açık her instance'ı çalıştırır, çökerse yeniden başlatır.",
    "off": "kapalı",
    "block": "girişleri engelle",
    "flatten": "hepsini kapat",
    "entries blocked": "girişler engelli",
    "flattening": "kapatılıyor",
    "Mode": "Mod",
    "live trading": "canlı işlem",
    "ENABLED": "AÇIK",
    "locked": "kilitli",
    "Live trading can only be enabled in config.py.": "Canlı işlem yalnızca config.py'den açılabilir.",
    "{n} stopped hidden": "{n} durdurulmuş gizli",
    "Max DD": "Maks. DD",
    "Buy &amp; hold": "Al ve tut",
    "Buy & hold": "Al ve tut",
    "Realized PnL": "Gerçekleşmiş PnL",
    "from closed trades, USDT": "kapanan işlemlerden, USDT",
    "Return": "Getiri",
    "{days} days · {closed} closed trades · {open} open": "{days} gün · {closed} kapanan işlem · {open} açık",
    "No running instances. Turn on Show stopped to see the others.":
        "Çalışan instance yok. Diğerlerini görmek için Durdurulanları göster'i açın.",
    "No instances yet.": "Henüz instance yok.",
    "Kill switch: {mode}": "Kill switch: {mode}",
    "Click Flatten again within 5 seconds to close every testnet/live position.":
        "Bütün testnet/canlı pozisyonları kapatmak için 5 saniye içinde Hepsini kapat'a tekrar tıklayın.",
    "Newest first": "Önce en yeni",
    "Oldest first": "Önce en eski",
    "Highest realized PnL": "En yüksek gerçekleşmiş PnL",
    "Highest return %": "En yüksek getiri %",
    "Highest Sharpe": "En yüksek Sharpe",
    "Lowest max drawdown": "En düşük maks. drawdown",
    "Highest win rate": "En yüksek win rate",
    "Most closed trades": "En çok kapanan işlem",
    "Name A–Z": "Ad A–Z",

    # ------------------------------------------------------------ instances list and form
    "Each instance is one strategy setup running in its own process under {cmd}. Its setup can't be changed "
    "after creation, so its results always describe one setup. To try something else, create another instance.":
        "Her instance, {cmd} altında kendi sürecinde çalışan tek bir strateji kurulumudur. Kurulum oluşturulduktan "
        "sonra değiştirilemez, bu yüzden sonuçları her zaman tek bir kurulumu anlatır. Başka bir şey denemek için "
        "yeni bir instance oluşturun.",
    "Name": "Ad",
    "Status": "Durum",
    "Setup": "Kurulum",
    "TF": "TF",
    "Symbols": "Semboller",
    "B&amp;H": "B&amp;H",
    "PnL": "PnL",
    "Realized PnL of closed trades, USDT": "Kapanan işlemlerin gerçekleşmiş PnL'i, USDT",
    "Trades": "İşlemler",
    "lower-timeframe rules": "alt timeframe kuralları",
    ", SMA{v} mode": ", SMA{v} modu",
    "trend filter: close above SMA{v}": "trend filtresi: kapanış SMA{v} üstünde",
    "Clone: a new instance with this setup": "Klonla: bu kurulumla yeni bir instance",
    "Restart": "Yeniden başlat",
    "[button] Stop": "Durdur",
    "Start": "Başlat",
    "2-60 characters: letters, digits, '-' and '_'. The setup can't be changed after creation: "
    "to try something else, create another instance.":
        "2-60 karakter: harf, rakam, '-' ve '_'. Kurulum oluşturulduktan sonra değiştirilemez: başka bir şey "
        "denemek için yeni bir instance oluşturun.",
    "e.g. {example}": "örn. {example}",
    "paper: simulated on live Binance prices. testnet: real market orders on the Binance Spot Testnet; "
    "one testnet instance can run at a time.":
        "paper: canlı Binance fiyatlarıyla simülasyon. testnet: Binance Spot Testnet'te gerçek market emirleri; "
        "aynı anda tek bir testnet instance'ı çalışabilir.",
    "Timeframe": "Timeframe",
    "Candle length the strategy trades on. Signals use closed candles only.":
        "Stratejinin işlem yaptığı mum uzunluğu. Sinyaller yalnızca kapanmış mumları kullanır.",
    "Strategy": "Strateji",
    "Stop": "Stop",
    "Parameters": "Parametreler",
    "Empty fields use the default shown.": "Boş alanlar gösterilen varsayılanı kullanır.",
    "Equity per symbol (USDT)": "Sembol başına sermaye (USDT)",
    "Sizing": "Boyutlama",
    "Trailing exit (ATR ×)": "Trailing çıkış (ATR ×)",
    "Sell at the next open when the close falls this many ATRs below the highest close since entry. "
    "Empty = off. Paper only.":
        "Kapanış, girişten bu yana en yüksek kapanışın bu kadar ATR altına düşünce sonraki açılışta sat. "
        "Boş = kapalı. Yalnızca paper.",
    "All default symbols: the list in config.SYMBOLS. Custom: your own list, e.g. BTC/USDT, ETH/USDT.":
        "Bütün varsayılan semboller: config.SYMBOLS'taki liste. Özel: kendi listeniz, örn. BTC/USDT, ETH/USDT.",
    "All default symbols": "Bütün varsayılan semboller",
    "Custom list": "Özel liste",
    "Lower timeframes": "Alt timeframe'ler",
    "Entry confirmation (E)": "Giriş onayı (E)",
    "Early exit (X)": "Erken çıkış (X)",
    "strategy signal": "strateji sinyali",
    "Empty: E and X read the strategy's own signal on the lower timeframes. A length N: they read whether "
    "the close is above (BUY) or below (SELL) SMA(N) of those candles. With v9 fast=1, slow=50, N=50 is the "
    "same as empty.":
        "Boş: E ve X alt timeframe'lerde stratejinin kendi sinyalini okur. Bir N uzunluğu: kapanışın o mumların "
        "SMA(N)'inin üstünde (BUY) mi altında (SELL) mı olduğunu okurlar. v9 fast=1, slow=50 ile N=50 boşla aynıdır.",
    "Trend filter (SMA)": "Trend filtresi (SMA)",
    "A BUY at the close enters only if that close is above SMA(N) of the closes, e.g. 200. Exits are not "
    "affected. Empty = off. Paper only.":
        "Kapanıştaki BUY, yalnızca o kapanış kapanışların SMA(N)'inin üstündeyse girer, örn. 200. Çıkışlar "
        "etkilenmez. Boş = kapalı. Yalnızca paper.",
    "Manual close": "Manuel kapatma",
    "Adds a Close button to open positions: the instance sells at the market within a minute. A manual "
    "close counts in this instance's results (reason MANUAL) and the next entry waits as after a trailing exit. "
    "Paper only.":
        "Açık pozisyonlara Kapat butonu ekler: instance bir dakika içinde piyasa fiyatından satar. Manuel "
        "kapatma bu instance'ın sonuçlarına dahil olur (neden MANUAL) ve sonraki giriş trailing çıkıştan "
        "sonraki gibi bekler. Yalnızca paper.",
    "Allow closing positions from the panel": "Pozisyonların panelden kapatılmasına izin ver",
    "Description": "Açıklama",
    "Start right away": "Hemen başlat",
    "Cancel": "İptal",
    "Create": "Oluştur",
    "{k} (exit on signals only)": "{k} (yalnızca sinyalle çıkış)",
    "New instance · copy of {name}": "Yeni instance · {name} kopyası",
    "USDT given to each symbol; the instance's return is over all symbols together.":
        "Her sembole verilen USDT; instance'ın getirisi bütün semboller birlikte hesaplanır.",
    "Research grid: {grid}": "Araştırma grid'i: {grid}",
    "No parameters.": "Parametre yok.",
    "Capital (USDT, split across symbols)": "Sermaye (USDT, sembollere bölünür)",
    "Capital in USDT, split equally across the symbols.": "USDT cinsinden sermaye, sembollere eşit bölünür.",
    "Stop-loss distance = ATR(14) × the multiple shown, checked against each candle's low (filled at the "
    "open on a gap).":
        "Stop-loss mesafesi = ATR(14) × gösterilen çarpan; her mumun en düşüğüne göre kontrol edilir (boşlukta "
        "açılıştan dolar).",
    "Testnet instances exit on signals only: exchange-side stops aren't implemented.":
        "Testnet instance'ları yalnızca sinyalle çıkar: borsa tarafı stop'lar henüz yok.",
    "none: exit on signals only.": "none: yalnızca sinyalle çıkış.",
    "Uses {lower} with the same strategy. E: a BUY at the {tf} close enters only if both say BUY too. "
    "X: sells inside the candle as soon as both say SELL (docs/intrabar-watch.md).":
        "Aynı stratejiyle {lower} kullanır. E: {tf} kapanışındaki BUY yalnızca ikisi de BUY diyorsa girer. "
        "X: ikisi de SELL der demez mumun içinde satar (docs/intrabar-watch.md).",
    "Paper instances on {tfs} only.": "Yalnızca {tfs} üzerindeki paper instance'lar.",
    " or ": " veya ",
    "Instance {name} created": "{name} instance'ı oluşturuldu",

    # ------------------------------------------------------------ instance page
    "Save": "Kaydet",
    "Saved": "Kaydedildi",
    "Return vs equal-weight buy & hold (%)": "Getiri ve eşit ağırlıklı al ve tut (%)",
    "Positions": "Pozisyonlar",
    "Signals": "Sinyaller",
    "Orders": "Emirler",
    "Events": "Olaylar",
    "Stop events": "Stop olayları",
    "Log": "Log",
    "Shadow stop: the first 1m candle that reached a trade's stop. Recorded only; the engine closes the "
    "trade at the main close at min(open, stop), as in the backtest. Times UTC.":
        "Gölge stop: bir işlemin stop'una ulaşan ilk 1m mum. Yalnızca kaydedilir; motor işlemi, backtest'teki "
        "gibi ana kapanışta min(açılış, stop) fiyatından kapatır. Saatler UTC.",
    "Newest first.": "En yeni üstte.",
    "Delete": "Sil",
    "buy &amp; hold {v}": "al ve tut {v}",
    "Max drawdown": "Maks. drawdown",
    "Closed trades": "Kapanan işlemler",
    "win rate {v}%": "win rate %{v}",
    "from closed trades": "kapanan işlemlerden",
    "Open positions": "Açık pozisyonlar",
    "{days} days · last {time}": "{days} gün · son {time}",
    "Time": "Zaman",
    "Symbol": "Sembol",
    "Price": "Fiyat",
    "Change": "Değişim",
    "Unrealized": "Gerçekleşmemiş",
    "before exit fee and slippage": "çıkış fee'si ve slippage'dan önce",
    "Side": "Yön",
    "Entry time": "Giriş zamanı",
    "Entry price": "Giriş fiyatı",
    "Qty": "Miktar",
    "Cost": "Maliyet",
    "No open positions.": "Açık pozisyon yok.",
    "Entry": "Giriş",
    "Exit": "Çıkış",
    "Exit price": "Çıkış fiyatı",
    "open: change since entry": "açık: girişten bu yana değişim",
    "Reason": "Neden",
    "open": "açık",
    "Filled": "Dolan",
    "Avg price": "Ort. fiyat",
    "Ref price": "Ref. fiyat",
    "Error": "Hata",
    "No trades yet.": "Henüz işlem yok.",
    "No orders yet.": "Henüz emir yok.",
    "Candle": "Mum",
    "Signal": "Sinyal",
    "RSI": "RSI",
    "Close": "Kapanış",
    "Position": "Pozisyon",
    "Action": "Eylem",
    "Level": "Seviye",
    "Message": "Mesaj",
    "No signals yet.": "Henüz sinyal yok.",
    "No events yet.": "Henüz olay yok.",
    "Hit": "Tetiklendi",
    "1m open": "1m açılış",
    "1m low": "1m en düşük",
    "Est. fill": "Tahmini dolum",
    "Fill vs stop": "Dolum / stop",
    "Noticed after": "Fark edilme süresi",
    "Engine exit": "Motor çıkışı",
    "Engine vs est.": "Motor / tahmin",
    "No stop hits recorded.": "Kaydedilmiş stop tetiklenmesi yok.",
    "No log yet (logs are written when the instance runs under run_all.py).":
        "Henüz log yok (loglar instance run_all.py altında çalışınca yazılır).",
    "the instance sells within a minute": "instance bir dakika içinde satar",
    "closing…": "kapatılıyor…",
    "Sell at the market price now": "Şimdi piyasa fiyatından sat",
    "[button] Close": "Kapat",
    "Click Close again within 5 seconds to sell {symbol} at the market price. A manual close is recorded as "
    "MANUAL and counts in this instance's results.":
        "{symbol}'ı piyasa fiyatından satmak için 5 saniye içinde Kapat'a tekrar tıklayın. Manuel kapatma "
        "MANUAL olarak kaydedilir ve bu instance'ın sonuçlarına dahil olur.",
    "{symbol}: close requested; the instance sells within a minute":
        "{symbol}: kapatma istendi; instance bir dakika içinde satar",
    "Click Delete again within 5 seconds. Stored results are kept.":
        "5 saniye içinde Sil'e tekrar tıklayın. Kayıtlı sonuçlar silinmez.",

    # ------------------------------------------------------------ playground
    "Days": "Gün",
    "Include unused holdout data": "Kullanılmamış holdout verisini dahil et",
    "Off by default so experiments don't use up data kept for final tests.":
        "Denemeler son testlere ayrılan veriyi tüketmesin diye varsayılan olarak kapalı.",
    "Run backtest": "Backtest çalıştır",
    "Running…": "Çalışıyor…",
    "Same engine as the bots: signals on closed candles, fills at the next open, fees and slippage from "
    "Settings, long-only. Every run is logged. Many variants on the same data will find something that looks "
    "good by chance, so treat good results here as ideas to test on new data.":
        "Botlarla aynı motor: sinyaller kapanmış mumlarda, dolumlar sonraki açılışta, fee ve slippage "
        "Ayarlar'dan, yalnızca long. Her çalıştırma kaydedilir. Aynı veride denenen çok sayıda varyant şans "
        "eseri iyi görünen bir şey bulur; buradaki iyi sonuçları yeni veride test edilecek fikirler olarak görün.",
    "Pick a setup and run a backtest.": "Bir kurulum seçip backtest çalıştırın.",
    "Return (%)": "Getiri (%)",
    "strategy": "strateji",
    "buy & hold": "al ve tut",
    "History": "Geçmiş",
    "{k} (signals only)": "{k} (yalnızca sinyaller)",
    "{v}% win": "%{v} kazanç",
    "Profit factor": "Profit factor",
    "Exposure": "Piyasada kalma",
    "Exit reason": "Çıkış nedeni",
    "No trades in this window.": "Bu aralıkta işlem yok.",
    "{n} runs logged": "{n} çalıştırma kayıtlı",
    "Params": "Parametreler",
    "Window": "Aralık",
    "No runs yet.": "Henüz çalıştırma yok.",
    "Data after {date} is an unused {timeframe} holdout and was left out.":
        "{date} sonrası veri kullanılmamış bir {timeframe} holdout'u olduğu için dışarıda bırakıldı.",

    # ------------------------------------------------------------ backtests
    "Results of {cmd} (grid backtest with default parameters). Research results from {research} are in "
    "the docs folder.":
        "{cmd} sonuçları (varsayılan parametrelerle grid backtest). {research} araştırma sonuçları docs "
        "klasöründe.",
    "Segment": "Segment",
    "Apply": "Uygula",
    "Clear": "Temizle",
    "Aggregate (median return per strategy)": "Toplu (strateji başına medyan getiri)",
    "Return%": "Getiri%",
    "BuyHold%": "AlTut%",
    "WinRate%": "WinRate%",
    "ProfitFactor": "ProfitFactor",
    "MaxDD%": "MaksDD%",
    "Sharpe": "Sharpe",
    "Exposure%": "PiyasadaKalma%",
    "Median Return%": "Medyan getiri%",

    # ------------------------------------------------------------ settings page
    "Save changes": "Değişiklikleri kaydet",
    "Changes are stored in {file} and override the defaults in {config}. Strategy setups belong to instances "
    "(see Instances). Locked settings can only be changed in {short}.":
        "Değişiklikler {file} dosyasında tutulur ve {config} içindeki varsayılanların yerine geçer. Strateji "
        "kurulumları instance'lara aittir (bkz. Instance'lar). Kilitli ayarlar yalnızca {short} içinde "
        "değiştirilebilir.",
    "Locked": "Kilitli",
    "changed": "değişti",
    "reset": "sıfırla",
    "Restarts: {list}.": "Yeniden başlatır: {list}.",
    "API keys": "API anahtarları",
    "set": "var",
    "not set": "yok",
    "Binance API key": "Binance API anahtarı",
    "Testnet API key": "Testnet API anahtarı",
    "Telegram bot": "Telegram botu",
    "Keys are kept in {env} and are never shown or edited here. Telegram: see {cmd}.":
        "Anahtarlar {env} dosyasında tutulur, burada asla gösterilmez ya da düzenlenmez. Telegram için: {cmd}.",
    "Send test message": "Test mesajı gönder",
    "Nothing changed": "Hiçbir şey değişmedi",
    "Settings saved": "Ayarlar kaydedildi",
    "Running {list} instances use the old values until they restart.":
        "Çalışan {list} instance'ları yeniden başlayana kadar eski değerleri kullanır.",
    "Restart them now": "Şimdi yeniden başlat",
    "Test message sent": "Test mesajı gönderildi",
    "Restarting: {list}": "Yeniden başlatılıyor: {list}",
    "No running instances affected": "Etkilenen çalışan instance yok",
    # settings.SCHEMA: groups, labels and help
    "Costs": "Maliyetler",
    "Universe": "Evren",
    "Paper trading": "Paper trading",
    "Notifications": "Bildirimler",
    "Backtest & research": "Backtest ve araştırma",
    "Taker fee rate": "Taker fee oranı",
    "Fee per fill as a fraction (0.001 = 0.1%). Used by backtests and paper trading.":
        "Dolum başına fee, kesir olarak (0.001 = %0,1). Backtest'ler ve paper trading kullanır.",
    "Slippage (bps)": "Slippage (bps)",
    "Adverse slippage per fill in basis points (1 bps = 0.01%).": "Dolum başına aleyhte slippage, baz puan (1 bps = %0,01).",
    "Default symbol list (BASE/QUOTE). Instances use it unless they define their own.":
        "Varsayılan sembol listesi (BASE/QUOTE). Instance'lar kendi listelerini tanımlamadıkça bunu kullanır.",
    "Default equity per symbol (USDT)": "Sembol başına varsayılan sermaye (USDT)",
    "Starting paper equity per symbol for new instances.": "Yeni instance'lar için sembol başına başlangıç paper sermayesi.",
    "Allow short positions": "Short pozisyonlara izin ver",
    "Spot markets can't be shorted, so this stays off.": "Spot piyasada short açılamaz, bu yüzden kapalı kalır.",
    "Trading capital (USDT)": "İşlem sermayesi (USDT)",
    "Total budget of the testnet instance, split equally across its symbols.":
        "Testnet instance'ının toplam bütçesi, sembollerine eşit bölünür.",
    "Max order size (USDT)": "Maks. emir büyüklüğü (USDT)",
    "Hard cap per order.": "Emir başına kesin üst sınır.",
    "Daily loss limit (%)": "Günlük zarar limiti (%)",
    "After this loss within a UTC day no new entries are made until the next day.":
        "Bir UTC gününde bu kadar zarardan sonra ertesi güne kadar yeni giriş yapılmaz.",
    "Trading mode": "İşlem modu",
    "Only the testnet can be used from the dashboard.": "Panelden yalnızca testnet kullanılabilir.",
    "Live trading enabled": "Canlı işlem açık",
    "Can only be switched on in config.py, on purpose.": "Bilerek yalnızca config.py'den açılabilir.",
    "Trade messages": "İşlem mesajları",
    "Telegram message when positions open or close and for testnet orders. Errors, crashes and the kill "
    "switch are always reported.":
        "Pozisyon açılıp kapandığında ve testnet emirlerinde Telegram mesajı. Hatalar, çökmeler ve kill switch "
        "her zaman bildirilir.",
    "Daily summary": "Günlük özet",
    "One message a day with every instance's return, buy-and-hold and drawdown.":
        "Her instance'ın getirisi, al ve tut'u ve drawdown'uyla günde bir mesaj.",
    "Daily summary hour (UTC)": "Günlük özet saati (UTC)",
    "Hour of the day the summary is sent (6 UTC = 09:00 in Turkey).": "Özetin gönderildiği saat (6 UTC = Türkiye'de 09:00).",
    "Grid backtest history (days)": "Grid backtest geçmişi (gün)",
    "History length of the grid backtest.": "Grid backtest'in geçmiş uzunluğu.",
    "Out-of-sample fraction": "Out-of-sample oranı",
    "Share of the history kept out-of-sample in the grid backtest.": "Grid backtest'te out-of-sample ayrılan geçmiş payı.",
    "Walk-forward folds": "Walk-forward katları",
    "Number of walk-forward test windows in the research runner.": "Araştırma çalıştırıcısındaki walk-forward test penceresi sayısı.",
    "Min trades per training window": "Eğitim penceresi başına min. işlem",
    "Minimum trades before a training window's Sharpe is trusted.":
        "Bir eğitim penceresinin Sharpe'ına güvenmek için gereken en az işlem sayısı.",
    "Cost stress multiplier": "Maliyet stres çarpanı",
    "Fees and slippage multiplier for the stress test.": "Stres testi için fee ve slippage çarpanı.",

    # ------------------------------------------------------------ strategies (core/strategy.py)
    "RSI + EMA crossover: buy oversold in an EMA uptrend, sell overbought in a downtrend.":
        "RSI + EMA kesişimi: EMA yükseliş trendinde aşırı satımda al, düşüş trendinde aşırı alımda sat.",
    "Like v1, but the condition must hold on the last `confirm` candles.":
        "v1 gibi, ama koşul son `confirm` mumda sağlanmalı.",
    "RSI + EMA with looser thresholds than v1.": "RSI + EMA, v1'den gevşek eşiklerle.",
    "Bollinger Band mean reversion with an RSI filter.": "RSI filtreli Bollinger Bandı ortalamaya dönüş.",
    "EMA cross trading only in the trend direction, with an RSI filter.":
        "Yalnızca trend yönünde EMA kesişimi, RSI filtresiyle.",
    "Donchian channel breakout with an ATR threshold against false breakouts.":
        "Donchian kanalı kırılımı, sahte kırılımlara karşı ATR eşiğiyle.",
    "Trend filter (EMA fast vs slow) plus an RSI pullback-and-cross entry.":
        "Trend filtresi (hızlı ve yavaş EMA) artı RSI geri çekilme ve kesişim girişi.",
    "Time-series momentum: long while the return over `lookback` candles is positive.":
        "Zaman serisi momentumu: `lookback` mumdaki getiri pozitifken long.",
    "Moving-average regime: long while SMA(fast) is above SMA(slow); fast=1 is the close. entry_band "
    "(e.g. 0.02) asks for that much above SMA(slow) to enter.":
        "Hareketli ortalama rejimi: SMA(fast), SMA(slow)'un üstündeyken long; fast=1 kapanışın kendisidir. "
        "entry_band (örn. 0.02) girmek için SMA(slow)'un o kadar üstünü ister.",
    "Turtle-style breakout: enter above the prior `entry`-bar high, exit below the `exit_len`-bar low.":
        "Turtle tarzı kırılım: önceki `entry` mumun en yükseğinin üstünde gir, `exit_len` mumun en düşüğünün "
        "altında çık.",
    # parameters (strategy.PARAM_INFO)
    "Buy when RSI(14) is below this, while the fast EMA is above the slow one.":
        "Hızlı EMA yavaşın üstündeyken RSI(14) bunun altındaysa al.",
    "Sell when RSI(14) is above this, while the fast EMA is below the slow one.":
        "Hızlı EMA yavaşın altındayken RSI(14) bunun üstündeyse sat.",
    "Fast EMA length, in candles.": "Hızlı EMA uzunluğu, mum.",
    "Slow EMA length, in candles.": "Yavaş EMA uzunluğu, mum.",
    "Candles in a row the buy or sell condition must hold.": "Al ya da sat koşulunun art arda sağlanması gereken mum sayısı.",
    "Bollinger Band length, in candles.": "Bollinger Bandı uzunluğu, mum.",
    "Band width, in standard deviations.": "Bant genişliği, standart sapma.",
    "A close below the lower band buys only if RSI(14) is below this.":
        "Alt bandın altındaki kapanış yalnızca RSI(14) bunun altındaysa alır.",
    "A close above the upper band sells only if RSI(14) is above this.":
        "Üst bandın üstündeki kapanış yalnızca RSI(14) bunun üstündeyse satar.",
    "Slow EMA length, in candles; the close must be on its side too.": "Yavaş EMA uzunluğu, mum; kapanış da onun tarafında olmalı.",
    "A buy needs RSI(14) above this.": "Alım için RSI(14) bunun üstünde olmalı.",
    "A sell needs RSI(14) below this.": "Satış için RSI(14) bunun altında olmalı.",
    "Donchian channel length, in candles.": "Donchian kanalı uzunluğu, mum.",
    "A breakout must clear the channel by this many ATRs.": "Kırılım kanalı bu kadar ATR aşmalı.",
    "Fast EMA of the trend filter, in candles.": "Trend filtresinin hızlı EMA'sı, mum.",
    "Slow EMA of the trend filter, in candles.": "Trend filtresinin yavaş EMA'sı, mum.",
    "RSI(14) must have been below this within the last `lookback` candles before a buy.":
        "Alımdan önce son `lookback` mum içinde RSI(14) bunun altına inmiş olmalı.",
    "Buy when RSI(14) crosses back above this.": "RSI(14) bunun üstüne geri çıkınca al.",
    "RSI(14) must have been above this within the last `lookback` candles before a sell.":
        "Satıştan önce son `lookback` mum içinde RSI(14) bunun üstüne çıkmış olmalı.",
    "Sell when RSI(14) crosses back below this.": "RSI(14) bunun altına geri inince sat.",
    "How many candles back to look for the oversold or overbought RSI.":
        "Aşırı satım ya da aşırı alım RSI'ı için geriye kaç mum bakılacağı.",
    "Return measured over this many candles: long while it is positive.": "Bu kadar mumda ölçülen getiri: pozitifken long.",
    "SMA length of the fast line, in candles; 1 means the close itself.": "Hızlı çizginin SMA uzunluğu, mum; 1 kapanışın kendisi demek.",
    "SMA length of the regime line, in candles: long above it, exit below it.":
        "Rejim çizgisinin SMA uzunluğu, mum: üstünde long, altında çıkış.",
    "Enter only this fraction above SMA(slow), e.g. 0.02 = 2%. The exit stays at SMA(slow).":
        "Yalnızca SMA(slow)'un bu oran kadar üstünde gir, örn. 0.02 = %2. Çıkış SMA(slow)'da kalır.",
    "Buy when the close breaks above the highest high of this many previous candles.":
        "Kapanış önceki bu kadar mumun en yükseğinin üstüne çıkınca al.",
    "Sell when the close breaks below the lowest low of this many previous candles.":
        "Kapanış önceki bu kadar mumun en düşüğünün altına inince sat.",
    # sizings (instances.SIZINGS)
    "Equal capital per symbol, never rebalanced": "Sembol başına eşit sermaye, hiç yeniden dengelenmez",
    "Monthly rebalanced to 1/volatility weights (docs/v9-sizing-test.md)":
        "Her ay 1/volatilite ağırlıklarına göre yeniden dengelenir (docs/v9-sizing-test.md)",

    # ------------------------------------------------------------ server errors (templates)
    "missing request header": "istek başlığı eksik",
    "mode must be off, block or flatten": "mod off, block ya da flatten olmalı",
    "action must be start, stop, restart, describe or favorite": "action start, stop, restart, describe ya da favorite olmalı",
    "unknown table {table}": "bilinmeyen tablo {table}",
    "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set in .env": ".env'de TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanımlı değil",
    # instances.py
    "{strategy} has no parameter '{name}'": "{strategy} stratejisinin '{name}' parametresi yok",
    "parameter {name}: '{value}' is not a number": "{name} parametresi: '{value}' bir sayı değil",
    "parameter {name} must be a whole number": "{name} parametresi tam sayı olmalı",
    "parameter {name} must not be negative": "{name} parametresi negatif olmamalı",
    "name: 2-60 characters: letters, digits, '-' and '_', starting with a letter or digit":
        "ad: 2-60 karakter: harf, rakam, '-' ve '_'; harf ya da rakamla başlamalı",
    "mode must be one of {list}": "mod şunlardan biri olmalı: {list}",
    "unknown strategy {name}": "bilinmeyen strateji {name}",
    "timeframe must be one of {list}": "timeframe şunlardan biri olmalı: {list}",
    "stop must be one of {list}": "stop şunlardan biri olmalı: {list}",
    "testnet instances can't use a stop yet (exchange-side stop orders aren't implemented)":
        "testnet instance'ları henüz stop kullanamaz (borsa tarafı stop emirleri yok)",
    "trailing exit must be a number (ATR multiple) or empty": "trailing çıkış bir sayı (ATR çarpanı) ya da boş olmalı",
    "trailing exit must be between 0.5 and 20 ATR": "trailing çıkış 0.5 ile 20 ATR arasında olmalı",
    "only paper instances can use a trailing exit for now": "şimdilik yalnızca paper instance'lar trailing çıkış kullanabilir",
    "{field} must be true or false": "{field} true ya da false olmalı",
    "only paper instances can use lower-timeframe rules for now":
        "şimdilik yalnızca paper instance'lar alt timeframe kuralları kullanabilir",
    "lower-timeframe rules need a {tfs} timeframe": "alt timeframe kuralları {tfs} timeframe'i gerektirir",
    "{field} must be a whole number or empty": "{field} tam sayı ya da boş olmalı",
    "{field} must be a whole number from {low} to {high}": "{field} {low} ile {high} arasında tam sayı olmalı",
    "the lower-timeframe SMA needs lower-timeframe entry or exit": "alt timeframe SMA'sı alt timeframe giriş ya da çıkışı gerektirir",
    "only paper instances can use the trend filter for now": "şimdilik yalnızca paper instance'lar trend filtresi kullanabilir",
    "only paper positions can be closed from the panel (testnet: use the kill switch)":
        "panelden yalnızca paper pozisyonları kapatılabilir (testnet: kill switch'i kullanın)",
    "sizing must be one of {list}": "boyutlama şunlardan biri olmalı: {list}",
    "only paper instances can use portfolio sizing for now": "şimdilik yalnızca paper instance'lar portföy boyutlaması kullanabilir",
    "invalid symbols: {list}": "geçersiz semboller: {list}",
    "equity must be a number": "sermaye bir sayı olmalı",
    "equity must be between 10 and 10,000,000 USDT": "sermaye 10 ile 10.000.000 USDT arasında olmalı",
    "only one testnet instance can run at a time ({name} is running): they share one exchange account":
        "aynı anda yalnızca bir testnet instance'ı çalışabilir ({name} çalışıyor): tek bir borsa hesabını paylaşırlar",
    "no instance named '{name}'": "'{name}' adlı instance yok",
    "an instance named '{name}' already exists": "'{name}' adlı bir instance zaten var",
    "{fields} can't be changed after creation; create a new instance":
        "{fields} oluşturulduktan sonra değiştirilemez; yeni bir instance oluşturun",
    "field '{field}' can't be changed": "'{field}' alanı değiştirilemez",
    "stop the instance before deleting it": "silmeden önce instance'ı durdurun",
    # manual_close.py
    "{name} was created without manual close": "{name} manuel kapatma olmadan oluşturuldu",
    "start the instance first: its process does the closing": "önce instance'ı başlatın: kapatmayı onun süreci yapar",
    "{name} has no open {symbol} position": "{name} instance'ında açık {symbol} pozisyonu yok",
    "closing {symbol} was already requested": "{symbol} kapatması zaten istendi",
    # playground.py
    "unknown stop {stop}": "bilinmeyen stop {stop}",
    "{days} days of {timeframe} is {candles} candles; allowed 10-{max}":
        "{days} günlük {timeframe} {candles} mum ediyor; izin verilen 10-{max}",
    "no data for {symbol} {timeframe}": "{symbol} {timeframe} için veri yok",
    "not enough candles in the selected window": "seçilen aralıkta yeterli mum yok",
    # settings.py
    "{label}: must be one of {list}": "{label}: şunlardan biri olmalı: {list}",
    "{label}: '{symbol}' is not a BASE/QUOTE symbol": "{label}: '{symbol}' BASE/QUOTE biçiminde bir sembol değil",
    "{label}: at least one symbol is required": "{label}: en az bir sembol gerekli",
    "{label}: invalid value {value}": "{label}: geçersiz değer {value}",
    "{label}: must be at least {min}": "{label}: en az {min} olmalı",
    "{label}: must be at most {max}": "{label}: en fazla {max} olmalı",
    "unknown setting {key}": "bilinmeyen ayar {key}",
    "{label} can't be changed from the dashboard": "{label} panelden değiştirilemez",
}
