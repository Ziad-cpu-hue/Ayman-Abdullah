"""تحويل الأسماء العربية إلى الإنجليزية (بالنطق المصري الشائع) دون أي اتصال بالإنترنت.

الطريقة: قاموس للأسماء الشائعة (يعطي الإملاء المعتاد مثل Mohamed / Ahmed)، وإن لم يوجد الاسم
في القاموس يُحوَّل حرفًا بحرف بقواعد بسيطة مع إضافة حركة بين الحروف الساكنة المتجاورة.
"""
import re

_AR = re.compile(r"[\u0600-\u06FF]")
_TASHKEEL = re.compile(r"[\u064B-\u065F\u0670\u0640]")


def has_arabic(text):
    return bool(_AR.search(text or ""))


def _norm(word):
    w = _TASHKEEL.sub("", word)
    for a in "أإآٱ":
        w = w.replace(a, "ا")
    return w.replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي").replace("ء", "")


def _key(word):
    return _norm(word).replace("ة", "ه")


_NAMES_RAW = """
محمد Mohamed|احمد Ahmed|محمود Mahmoud|مصطفى Mostafa|علي Ali|عمر Omar|يوسف Youssef|ابراهيم Ibrahim|اسماعيل Ismail
خالد Khaled|حسن Hassan|حسين Hussein|حسام Hossam|كريم Kareem|طارق Tarek|عمرو Amr|ايمن Ayman|شريف Sherif|وليد Walid
وائل Wael|ياسر Yasser|يحيى Yahya|زياد Ziad|زيد Zaid|سامح Sameh|سامي Samy|سمير Samir|سعيد Saeed|سعد Saad|سيد Sayed
شادي Shady|شهاب Shehab|تامر Tamer|طه Taha|مازن Mazen|مروان Marwan|ماجد Maged|مجدي Magdy|نبيل Nabil|نادر Nader
ناصر Nasser|ناجي Nagy|اسامة Osama|عثمان Othman|هشام Hesham|هاني Hany|هيثم Haitham|ايهاب Ehab|اسلام Islam|انس Anas
انور Anwar|اشرف Ashraf|اكرم Akram|امير Amir|امين Amin|باسم Basem|باسل Basel|بلال Belal|بهاء Bahaa|جمال Gamal
جاد Gad|جميل Gamil|حازم Hazem|حمدي Hamdy|حمزة Hamza|حسني Hosny|خليل Khalil|رضا Reda|رمضان Ramadan|رامي Ramy
رامز Ramez|رجب Ragab|رأفت Raafat|رشاد Rashad|زكي Zaky|زكريا Zakaria|سيف Seif|سلمان Salman|سليمان Soliman
سلامة Salama|صلاح Salah|صبري Sabry|صالح Saleh|ضياء Diaa|عادل Adel|عاطف Atef|عبده Abdo|عصام Essam|عماد Emad
عمار Ammar|عزت Ezzat|عوض Awad|غريب Ghareeb|فادي Fady|فارس Fares|فتحي Fathy|فريد Farid|فؤاد Fouad|فيصل Faisal
قاسم Kasem|كمال Kamal|لؤي Louay|ماهر Maher|مالك Malek|مؤمن Moamen|محسن Mohsen|مختار Mokhtar|مدحت Medhat
مراد Mourad|مرسي Morsy|معاذ Moaz|معتز Moataz|منصور Mansour|منير Mounir|مهند Mohanad|مهدي Mahdy|مينا Mina
ميخائيل Michael|نصر Nasr|نعمان Noaman|نور Nour|نوح Noah|هادي Hady|هاشم Hashem|وسيم Wassim|وجدي Wagdy|ياسين Yassin
يونس Younes|يعقوب Yaqoub|يزن Yazan|كيرلس Kirollos|بيشوي Bishoy|مارك Mark|جورج George|بيتر Peter|مايكل Michael
حمادة Hamada|طلعت Talaat|عزيز Aziz|صابر Saber|فاروق Farouk|شعبان Shaaban|بدر Badr|بسام Bassam|براء Baraa
توفيق Tawfik|ثروت Tharwat|جلال Galal|حاتم Hatem|حافظ Hafez|خيري Khairy|دياب Diab|راشد Rashed|ربيع Rabie
رفعت Refaat|سامر Samer|سراج Serag|شوقي Shawky|عابد Abed|عاصم Assem|علاء Alaa|غانم Ghanem|فهد Fahd|فوزي Fawzy
قدري Kadry|لطفي Lotfy|متولي Metwally|محب Mohib|مرتضى Mortada|مسعد Mosaad|معوض Moawad|نجيب Naguib|نشأت Nashaat
هلال Helal|وحيد Waheed|يسري Yousry|ايمان Eman|مجد Magd|ادم Adam|ايوب Ayman|عمرو Amr|ادهم Adham|ابراهيم Ibrahim
فاطمة Fatma|مريم Mariam|سارة Sara|سلمى Salma|ندى Nada|نوران Nouran|هنا Hana|حنان Hanan|هبة Heba|هاجر Hagar
حبيبة Habiba|فرح Farah|ايه Aya|اية Aya|اسماء Asmaa|اسراء Esraa|دعاء Doaa|دينا Dina|داليا Dalia|رانيا Rania
رنا Rana|ريم Reem|رحمة Rahma|رضوى Radwa|روان Rawan|شيرين Shereen|شيماء Shaimaa|سندس Sondos|تسنيم Tasneem
يارا Yara|ياسمين Yasmin|يمنى Yomna|ملك Malak|منة Menna|مي Mai|منى Mona|مروة Marwa|ميرنا Mirna|ميار Mayar
مها Maha|ماجدة Magda|نهى Noha|نجلاء Naglaa|نادين Nadine|نسمة Nesma|نيفين Nevine|هدى Hoda|هالة Hala|هند Hind
وفاء Wafaa|ولاء Walaa|الاء Alaa|امل Amal|اميرة Amira|انجي Engy|بسمة Basma|بسنت Basant|جنة Gannah|جنى Jana
جيهان Gehan|جميلة Gamila|حنين Haneen|خديجة Khadija|خلود Kholoud|رشا Rasha|رغد Raghad|رقية Roqaya|زينب Zeinab
زهرة Zahra|سناء Sanaa|سهى Soha|سمية Somaya|سمر Samar|سلوى Salwa|سماح Samah|شهد Shahd|شروق Shorouk|صفاء Safaa
ضحى Doha|عائشة Aisha|عبير Abeer|علياء Alyaa|غادة Ghada|غدير Ghadeer|فدوى Fadwa|فيروز Fayrouz|كريمة Karima
لينا Lina|ليلى Layla|لمياء Lamiaa|ليان Lian|لبنى Lobna|مايا Maya|مارينا Marina|ماريا Maria|ميرفت Mervat
نجوى Nagwa|نرمين Nermeen|نشوى Nashwa|نعمة Naema|نغم Nagham|هايدي Haidy|هيام Hiam|وردة Warda|يسرا Yousra
ريهام Reham|رحاب Rehab|سلسبيل Salsabil|سجى Saga|سيلا Sila|ملاك Malak|مرفت Mervat|منار Manar|مودة Mawadda
رحمن Rahman|فتاح Fattah|حميد Hamid|حليم Halim|رحيم Rahim|قادر Kader|لطيف Latif|منعم Moneim|باسط Basset
مجيد Magid|وهاب Wahab|سلام Salam|غني Ghany|رازق Razek|جواد Gawad|مقصود Maksoud|ستار Sattar|معطي Moaty
زهراء Zahraa|بن Bin|ابن Ibn|ابو Abou|ام Om|الدين El-Din|الله Allah
""".replace("\n", "|")

_NAMES = {}
for _item in _NAMES_RAW.split("|"):
    _item = _item.strip()
    if _item:
        _ar, _en = _item.split(" ", 1)
        _NAMES.setdefault(_key(_ar), _en.strip())

_CONS = {"ب": "b", "ت": "t", "ث": "th", "ج": "g", "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z",
         "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "غ": "gh", "ف": "f", "ق": "k", "ك": "k",
         "ل": "l", "م": "m", "ن": "n", "ه": "h", "پ": "b", "چ": "ch", "ڤ": "v", "گ": "g"}
_VOWEL_LETTERS = set("اوي") | {"ع", "ة"}


def _fallback(word):
    """تحويل حرفي بسيط لكلمة غير موجودة في القاموس."""
    w = _norm(word)
    out, prev_type = [], None  # prev_type: "C" حرف ساكن، "V" حرف علّة، None بداية الكلمة
    n = len(w)
    for i, ch in enumerate(w):
        nxt = w[i + 1] if i + 1 < n else ""
        if ch in _CONS:
            if prev_type == "C":
                out.append("a")
            if ch == "ه" and i == n - 1 and prev_type == "C":
                out.append("a")  # هاء/تاء مربوطة في آخر الاسم (…ة)
                prev_type = "V"
                continue
            out.append(_CONS[ch])
            prev_type = "C"
        elif ch == "ع":
            out.append("a")
            prev_type = "V"
        elif ch == "ا":
            out.append("a")
            prev_type = "V"
        elif ch == "ة":
            out.append("a")
            prev_type = "V"
        elif ch == "و":
            if i == 0 or prev_type == "V" or nxt in ("ا", "ي"):
                out.append("w")
            elif i == n - 1:
                out.append("o")
            else:
                out.append("ou")
            prev_type = "V"
        elif ch == "ي":
            if i == 0 or prev_type == "V" or i == n - 1:
                out.append("y")
            else:
                out.append("ee")
            prev_type = "V"
        elif _AR.match(ch):
            continue  # رموز عربية أخرى تُهمل
        else:
            out.append(ch)  # حروف لاتينية/أرقام/علامات تبقى كما هي
            prev_type = None
    s = "".join(out)
    return s[:1].upper() + s[1:]


def _cap(s):
    return "-".join(p[:1].upper() + p[1:] for p in s.split("-"))


def _word(word):
    k = _key(word)
    if k in _NAMES:
        return _NAMES[k]
    # «ال» التعريف: الشريف → El-Sherif
    if k.startswith("ال") and len(k) > 3:
        rest = k[2:]
        inner = _NAMES.get(rest) or _fallback(rest)
        return "El-" + _cap(inner)
    return _cap(_fallback(word))


def to_english_name(name):
    """يحوّل الاسم العربي إلى إنجليزي. الكلمات غير العربية تبقى كما كُتبت."""
    words = [w for w in (name or "").split() if w]
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if not has_arabic(w):
            out.append(w)
            i += 1
            continue
        k = _key(w)
        # عبد X → Abdel X (عبد الله → Abdullah)
        if k == "عبد" and i + 1 < len(words) and has_arabic(words[i + 1]):
            nk = _key(words[i + 1])
            if nk in ("الله", "اله"):
                out.append("Abdullah")
            else:
                base = nk[2:] if nk.startswith("ال") and len(nk) > 3 else nk
                out.append("Abdel" + (_NAMES.get(base) or _fallback(base)).lower())
            i += 2
            continue
        if k.startswith("عبد") and len(k) > 4 and k not in _NAMES:
            rest = k[3:]
            if rest in ("الله", "اله"):
                out.append("Abdullah")
            else:
                base = rest[2:] if rest.startswith("ال") and len(rest) > 3 else rest
                out.append("Abdel" + (_NAMES.get(base) or _fallback(base)).lower())
            i += 1
            continue
        # منة الله / هبة الله / نعمة الله → Mennatallah / Hebatallah ...
        if k in ("الله", "اله") and i > 0 and _key(words[i - 1]) in ("منه", "هبه", "نعمه", "رحمه") and out:
            out[-1] = out[-1] + "tallah"
            i += 1
            continue
        out.append(_word(w))
        i += 1
    return " ".join(out)


def apply_name_policy(name):
    """يطبّق سياسة المنصة على اسم مسجَّل: يعيد (الاسم المعروض، الاسم العربي الأصلي أو "").

    الافتراضي: إن كان الاسم عربيًا وإعداد «تحويل الأسماء للإنجليزية» مفعّلًا (وهو الافتراضي) يُخزَّن الاسم
    الإنجليزي ويُحفظ الأصل العربي للرجوع إليه.
    """
    from .models import SiteSetting

    name = " ".join((name or "").split())
    if has_arabic(name) and SiteSetting.get().names_in_english:
        en = to_english_name(name)[:150].strip()
        if en:
            return en, name[:150]
    return name, ""
