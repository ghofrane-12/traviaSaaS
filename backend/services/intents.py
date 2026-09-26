# services/intents.py
import re
GREETINGS = [
    # Français
    "bonjour", "bonsoir", "bonne journée", "bonne soirée", "bonne matinée",
    "bonjour à vous", "bien le bonjour", "salut", "coucou", "hey",
    "je vous salue", "ravi de vous voir", "enchanté", "bonne nuit",
    "bon après-midi", "à vos souhaits", "bienvenue", "bon retour",
    "ça va", "comment allez-vous", "comment ça va", "quoi de neuf",
    "hello (fr)", "rebonjour", "bien le bonsoir",

    # Anglais
    "hello", "hi", "hey there", "good morning", "good afternoon",
    "good evening", "good day", "howdy", "greetings", "what's up", "yo",
    "sup", "heyy", "hiya", "g'day", "nice to see you", "long time no see",
    "how are you", "how's it going", "what's new", "peace",

    # Arabe standard
    "مرحبا", "السلام عليكم", "أهلا", "أهلاً وسهلاً", "صباح الخير",
    "مساء الخير", "صباح النور", "مساء النور", "تحية طيبة", "السلام عليكم ورحمة الله",
    "وعليكم السلام", "أهلاً بك", "أهلاً بكم", "سعدنا برؤيتك", "يا هلا",
    "حياك الله", "نورت", "الله يحييك",

    # Arabe dialectal tunisien (arabe)
    "سلام", "آهلا", "أهلا", "لاباس", "لا باس", "مرحبا بيك", "مرحبا بيكم",
    "كيفاش", "كيف حالك", "شنو أخبارك", "واش راك", "كيداير", "السلامو عليكوم",
    "أهلا بيك", "يعيشك (مرحب)", "أهلا وسهلا بيك", "نورتي (نورتنا)",
    "لباس", "لباس عليك", "شنية الأحوال", "أحوالك شنيّة", "الك يوم",
    "صباح الورد", "مساء الورد", "صباح الفل", "مساء الفل",

    # Derja latin (Tunisian latin script)
    "salam", "ahla", "ahlan", "lebass", "le bass", "mar7ba bik", "mar7ba bikom",
    "kifech", "kife7 7alek", "chnou 5barek", "wech rak", "kidayer", "salamou 3likoum",
    "ahla bik", "3aychek (mar7ba)", "ahla w sahla bik", "nawarti (nawartna)",
    "lebass 3lik", "chniya el a7wel", "a7welek chniya", "elk youm", "sebet el kheir",
    "sebet en nour", "masa el kheir", "masa en nour", "sebet lward", "masa lward",
]

THANKS = [
    # Français
    "merci", "merci beaucoup", "merci bien", "merci infiniment",
    "je vous remercie", "c'est gentil", "c'est très gentil",
    "un grand merci", "mille mercis", "chapeau", "avec mes remerciements",
    "merci du fond du cœur", "toutes mes reconnaissances", "merci mille fois",
    "je te remercie", "merci à vous", "merci pour tout", "merci d'avance",
    "merci quand même", "c'est sympa", "c'est adorable", "je suis reconnaissant",

    # Anglais
    "thank you", "thanks", "many thanks", "thank you so much",
    "thanks a lot", "cheers", "much appreciated", "thx", "appreciate it",
    "thanks a million", "you're the best", "i owe you one", "thank you kindly",
    "thanks heaps", "thanks in advance", "grateful", "much obliged",

    # Arabe standard
    "شكرا", "شكراً جزيلاً", "أشكرك", "شكرا لك", "شكرا على مساعدتك",
    "جزاك الله خيرا", "بارك الله فيك", "شكراً موصولاً", "شكراً من القلب",
    "ألف شكر", "الشكر لله", "لا عدمناك", "مشكور", "يعطيك العافية",

    # Arabe dialectal tunisien (arabe)
    "يعيشك", "يسلمو", "مرسي", "مرسي بزاف", "يزاك الخير",
    "يعطيك الصحة", "ربي يحفظك", "بارك الله فيك برشا", "بزاف مرسي",
    "يعيشك برشا", "الله يعيشك", "سلمت يداك", "ربي يخليك", "ربي يبارك فيك",
    "ما تحرمش", "جزيت خيراً", "شكرا على روحك", "مرسي على المساعدة",

    # Derja latin
    "3aychek", "yeslamou", "mersi", "mersi barcha", "yezzek el 5ir",
    "ya3tik es sa77a", "rabi ye7fdek", "baraka llahou fik barcha", "barcha mersi",
    "3aychek barcha", "allah ya3aychek", "selmet yadek", "rabi y5allik", "rabi yebarek fik",
    "ma t7arramch", "jzit 5ayran", "mersi 3ala rwe7ek", "mersi 3ala l m3awna",
    "rabi yjazzik bil 5ir", "ja7chouk", "merci 3lik", "ta9rib 3lik",
]

FAREWELLS = [
    # Français
    "au revoir", "à bientôt", "à plus tard", "à tout à l'heure",
    "à la prochaine", "bonne continuation", "bonne journée",
    "bonne soirée", "prenez soin de vous", "salut (au revoir)",
    "bye bye", "ciao", "adieu", "à un de ces jours", "à demain",
    "à ce soir", "bonne nuit", "bon courage", "bon vent", "sur ce, je pars",

    # Anglais
    "goodbye", "bye", "see you", "see you later", "see you soon",
    "take care", "have a good day", "farewell", "good night", "catch you later",
    "so long", "see ya", "later", "peace out", "until next time",
    "have a nice day", "bye for now", "see you around", "take it easy",

    # Arabe standard
    "مع السلامة", "وداعاً", "إلى اللقاء", "في أمان الله",
    "تصبح على خير", "مع السلامة وإلى اللقاء", "أستودعكم الله",
    "الله معك", "سلام", "أراك لاحقاً", "إلى الملتقى", "بأمان الله",

    # Arabe dialectal tunisien (arabe)
    "بسلامة", "يسلمك", "تصبح على خير", "نشوفك", "حتى نلقاو",
    "سلام عليكم", "يحفظك ربي", "بسلامة برشا", "نشوفك عزيز",
    "ربي معاك", "في أمان الله", "نشوفك على خير", "بسلامة خير",
    "أركى", "أركى على خير", "سيبك مني", "نحن على خير",

    # Derja latin
    "bslama", "yeslamlek", "tsebbe7 ala 5ir", "nchoufek", "7atta nel9aw",
    "salam 3likom", "ye7fdek rabbi", "bslama barcha", "nchoufek 3ziz",
    "rabi m3ak", "fi aman ellah", "nchoufek 3la 5ir", "bslama 5ir",
    "arka", "arka 3la 5ir", "sibek menni", "ne7na 3la 5ir", "nchallah nchoufek",
    "7atta n'tla9aw", "tawa nchoufek", "sellem 3lik", "neslek 3lik",
]

HELP_REQUESTS = [
    # Français
    "aide", "aidez-moi", "j'ai besoin d'aide", "pouvez-vous m'aider",
    "est-ce que vous pouvez m'aider", "je ne sais pas", "comment ça marche",
    "que pouvez-vous faire", "quels sont vos services", "expliquez-moi",
    "je suis perdu", "je bloque", "je comprends rien", "à l'aide",
    "sauvez-moi", "un coup de main", "besoin d'assistance", "je cherche",
    "comment faire", "quelle est la procédure", "je galère",

    # Anglais
    "help", "help me", "i need help", "can you help me", "could you help me",
    "what can you do", "how does this work", "what are your services",
    "i don't understand", "guide me", "i'm lost", "i'm stuck", "save me",
    "give me a hand", "assist me", "i need assistance", "show me",
    "explain", "what should i do", "how to", "teach me",

    # Arabe standard
    "ساعدني", "أحتاج مساعدة", "هل يمكنك مساعدتي", "أريد مساعدة",
    "كيف يعمل هذا", "ما هي خدماتك", "ماذا تفعل", "هل تشرح لي",
    "أنا ضائع", "لا أفهم", "أرشدني", "أخبرني كيف", "ما العمل",
    "أحتاج إلى دعم", "حل لي المشكلة", "أرجوك ساعدني",

    # Arabe dialectal tunisien (arabe)
    "عاوني", "نحتاج مساعدة", "شنو تعمل", "كيفاش تخدم", "علّمني",
    "فهمني", "شنو خدماتك", "تقدر تعاوني", "أنا حائر", "أنا تايه",
    "ما فهمت حاجة", "دلّني", "قولي كيفاش", "شنو العمل", "حلها لي",
    "نحتاج منك خدمة", "أعاونك شوية", "وخر", "نجدني", "أختصرلي",

    # Derja latin
    "3aweni", "ne7tej m3awna", "chnou ta3mel", "kifech te5dem", "3allemni",
    "fehmni", "chnou 5edmtek", "te9der t3aweni", "ena 7ayer", "ena tayeh",
    "ma fhemt 7aja", "dallemni", "9ouli kifech", "chnou el 3amal", "7ellha li",
    "ne7tej mennek 5edma", "na3wenek chwaya", "wekker", "nejjedni", "5taserli",
    "nheb nafham", "mouch fahmen 7aja", "3awni bark", "fdali 3la 5atr",
]

POSITIVE_REACTIONS = [
    # Français
    "super", "parfait", "excellent", "très bien", "d'accord", "ok",
    "d'accord merci", "bien sûr", "oui", "c'est bon", "nickel", "top",
    "génial", "magnifique", "impeccable", "formidable", "bravo", "parfaitement",
    "tout à fait", "exactement", "cela marche", "je valide", "trop bien",
    "à la cool", "ça roule", "impec", "croix", "yes", "youpi",

    # Anglais
    "great", "perfect", "awesome", "ok", "okay", "sure", "yes",
    "sounds good", "got it", "cool", "amazing", "fantastic", "perfect thanks",
    "wonderful", "excellent", "brilliant", "lovely", "superb", "splendid",
    "yay", "woohoo", "alright", "roger that", "fine", "all good", "nice",

    # Arabe standard
    "ممتاز", "رائع", "جميل", "تمام", "حسنا", "موافق", "اوكي",
    "بالمئة", "عظيم", "ممتاز جداً", "رائع جداً", "جميل جدا", "أحسنت",
    "صحيح", "بالتأكيد", "نعم بالتأكيد", "فهذا رائع", "لا مشكلة", "جيد",

    # Arabe dialectal tunisien (arabe)
    "واو", "برشا مليح", "مزيان", "يخي", "هاك", "تمام برشا",
    "بارك الله فيك", "عجيب", "محلاها", "ممتاز برشا", "رائع برشا",
    "والله مزيان", "شاطور", "يا سلام", "أحسنت برشا", "مليح برشا",
    "ما شاء الله", "تبارك الله", "حقك عليا", "على راسي", "بدون زحمة",

    # Derja latin
    "waw", "barcha mlieh", "mzyan", "yi5i", "hak", "tamem barcha",
    "baraka llahou fik", "3jib", "7elaha", "memtaz barcha", "rawe3 barcha",
    "wellah mzyan", "chatour", "ya salam", "ahsent barcha", "mlieh barcha",
    "macha ellah", "tbaraka ellah", "7e9ek 3leya", "3la rasi", "bedoun ze7ma",
    "fhemt 3lik", "jamila", "miya fi miya", "taw behi", "sahha",
]
RESPONSES = {
    "greeting": "Bonjour ! Je suis votre assistant voyage. Comment puis-je vous aider ? ✈️",
    "thanks": "Avec plaisir ! N'hésitez pas si vous avez d'autres questions 😊",
    "farewell": "Au revoir ! Bon voyage ! 🌍",
    "help": "Je peux vous aider à réserver des vols, hôtels, excursions et bien plus. Que souhaitez-vous ?",
    "positive": "Parfait ! Comment puis-je vous aider davantage ?",
}
def detect_intent(text: str) -> str | None:
    text_lower = text.lower().strip()
    text_clean = re.sub(r'[^\w\s]', '', text_lower).strip()
    input_words = set(text_clean.split())

    if not input_words:
        return None

    ALL_INTENT_WORDS = set()
    for phrases in (GREETINGS, THANKS, FAREWELLS, HELP_REQUESTS, POSITIVE_REACTIONS):
        for phrase in phrases:
            normalized = re.sub(r'[^\w\s]', '', phrase.lower()).strip()
            for word in normalized.split():
                ALL_INTENT_WORDS.add(word)

    if not input_words.issubset(ALL_INTENT_WORDS):
        return None

    def score(phrases):
        words = set()
        for phrase in phrases:
            normalized = re.sub(r'[^\w\s]', '', phrase.lower()).strip()
            for w in normalized.split():
                words.add(w)
        return len(input_words & words)

    scores = {
        "greeting": score(GREETINGS),
        "thanks":   score(THANKS),
        "farewell": score(FAREWELLS),
        "help":     score(HELP_REQUESTS),
        "positive": score(POSITIVE_REACTIONS),
    }

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None