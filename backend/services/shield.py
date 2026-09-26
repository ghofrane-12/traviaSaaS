import re
import regex
import json
import unicodedata

def clean_text(text: str) -> str:
    """
    Nettoie une requête :
    - Supprime les emojis sans toucher aux chiffres (7, 9, 3 en derja)
    - Supprime les caractères spéciaux inutiles
    - Réduit les répétitions de lettres (bghiiiit → bghit, weeesh → wesh)
    - Normalise les espaces et l'alef arabe
    """
    result = []
    for char in text:
        cp = ord(char)
        if char.isdigit():
            result.append(char)
        elif 0x1F000 <= cp <= 0x1FFFF: pass  
        elif 0x2600  <= cp <= 0x27BF:  pass   
        elif 0xFE00  <= cp <= 0xFE0F:  pass   
        elif cp == 0x200D:             pass 
        else:
            result.append(char)
    text = ''.join(result)

    text = re.sub(r'[^\w\s\u0600-\u06FF\.,\!\?\-\'\"€$]', ' ', text)
    text = re.sub(r'([a-zA-Z])\1{2,}', r'\1', text)
    text = re.sub(r'([\u0600-\u06FF])\1{2,}', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    text = re.sub(r'[أإآ]', 'ا', text)
    text = re.sub(r'(\w)([.,!?;:])(\s|$)', r'\1\3', text)
    return text


LEET_MAP = str.maketrans({
    '@': 'a', '4': 'a', '3': 'e', '1': 'i', '!': 'i',
    '0': 'o', '5': 's', '$': 's', '+': 't', '8': 'b', '6': 'g',
})

def normalize_leet(text: str) -> str:
    """Convertit le leet speak pour détecter les insultes camouflées."""
    return text.lower().translate(LEET_MAP)


BLACKLIST_ITEMS = [
    # ── Derja tunisienne (complète) ──
    # Insultes de base
    ('bhim', True), ('sakhta', True), ('bel7a9', True), ('a9re3', True),
    ('ta7an', True), ('mziber', True), ('3arek', True), ('3arsa', True),
    ('9a7ba', True), ('za9zouq', True), ('mfasedh', True), ('m9loub', True),
    ('meznouq', True), ('3ayyer', True), ('7a9er', True), ('zendali', True),
    ('ma7chouch', True), ('na3al', True), ('yel3en', True), ('yel3an', True),
    ('zbala', True), ('3efsa', True), ('khesra', True), ('khaser', True),
    ('7aywen', True), ('ba9ra', True), ('7anesh', True), ('9erda', True),
    ('bhou', True), ('bhouwet', True), ('sayeb', True), ('mrayel', True),
    ('7achak', True), ('ma3andouch', True), ('bel3afsa', True), ('3afes', True),
    ('ta3ban', True), ('nadhif', True), ('bour9ou', True), ('9ahba', True),
    ('msokher', True), ('msakher', True), ('m3awar', True), ('m3awed', True),
    ('7a99', True), ('7a9ara', True), ('7a9erha', True), ('7a9rou', True),
    ('ma7qour', True), ('m7aqer', True), ('sta7far', True), ('t7a9er', True),
    
    # Nouvelles entrées Derja
    ('charmouta', True), ('charmout', True), ('zebbi', True),('zebi', True), ('zeb', True),
    ('kes', True), ('kess', True), ('9a3da', True), ('9a3ed', True),
    ('m7arreg', True), ('7arrag', True), ('khra', True), ('khraa', True),
    ('zokra', True), ('zokri', True), ('tounsi 9ahba', True),
    ('weld bled', True), ('bled 9ahba', True), ('bled s5oun', True),
    ('9ahbet bled', True), ('7aywan', True), ('7aywana', True),
    ('doukhane', True), ('d5ane', True), ('mchich', True), ('7mira', True),
    ('kaleb', True), ('kelba', True), ('drari 7ram', True),
    
    # Expressions Derja
    ('fel fom', False), ('weld el 7aram', False), ('bent el 7aram', False),
    ('bint el kahba', False), ('ibn el kahba', False), ('omek fi', False),
    ('babak fi', False), ('roh t7awel', False), ('roh tnayyek', False),
    ('el khara hedha', False), ('service el khara', False),
    ('weld el 9ahba', False), ('bent el 9ahba', False), ('ya weldi', False),
    ('ya benti', False), ('ya 7mar', False), ('ya kelb', False),
    ('ya 9alb', False), ('ya 7aywan', False), ('ya zbala', False),
    ('ro7 3afes', False), ('ro7 l7achak', False), ('allah yel3en', False),
    ('allah yel3an', False), ('na3al din', False), ('na3al 7atta', False),
    ('omek 9ahba', False), ('bouk 9ahba', False), ('khtek 9ahba', False),
    ('3aylet 9ahba', False), ('dar 9ahba', False), ('weld 3arsa', False),
    
    # ── Derja tounsia (arabe) 
    ('بهيم', True), ('سخطة', True), ('بالحق', True), ('أقرع', True),
    ('طحان', True), ('مزيبر', True), ('عارك', True), ('عرسة', True),
    ('قحبة', True), ('زقزوق', True), ('مفسدة', True), ('مقلوب', True),
    ('مزنوق', True), ('عيّر', True), ('حاقر', True), ('زنداني', True),
    ('محشوش', True), ('نعال', True), ('يلعن', True), ('يلعان', True),
    ('زبالة', True), ('عفسة', True), ('خسارة', True), ('خاسر', True),
    ('حيوان', True), ('بقرة', True), ('حنش', True), ('بهو', True),
    ('بهوات', True), ('صايب', True), ('مرايل', True), ('حاشك', True),
    ('بالعفسة', True), ('عافس', True), ('برقو', True), ('قحبة', True),
    ('مسخر', True), ('مسخّر', True), ('فرحت', True), ('نعت', True),
    ('نعتو', True), ('أمك', True), ('بابك', True), ('مقوى', True),
    ('منياك', True), ('منيوك', True), ('زامل', True), ('زوفري', True),
    ('حمار', True), ('كلب', True), ('خرا', True), ('زبل', True),
    ('كس', True), ('شرموطة', True), ('عرص', True), ('خنزير', True),
    
    # Nouvelles entrées arabe
    ('وسخ', True), ('قذر', True), ('نجس', True), ('خبيث', True),
    ('لئيم', True), ('وغد', True), ('داعر', True), ('فاجر', True),
    ('فاسق', True), ('منحرف', True), ('شاذ', True), ('أهبل', True),
    ('أبله', True), ('أحمق', True), ('معتوه', True), ('مخبول', True),
    ('مجنون', True), ('مختل', True), ('سافل', True), ('حقير', True),
    ('وضيع', True), ('دنيء', True), ('نذل', True), ('خسيس', True),
    
    # Expressions arabes
    ('كس أمك', False), ('كس بابك', False), ('ولد الحرام', False),
    ('بنت الحرام', False), ('ولد القحبة', False), ('بنت القحبة', False),
    ('أمك في', False), ('بابك في', False), ('روح تحول', False),
    ('روح تنيك', False), ('الخرا هذا', False), ('خدمة الخرا', False),
    ('هاذ الزبل', False), ('هاذ الزبالة', False), ('طيارة الخرا', False),
    ('شركة الخرا', False), ('ابن الكلب', False), ('ابنة الكلب', False),
    ('أمك قحبة', False), ('أبوك قحبة', False), ('أختك قحبة', False),
    ('خالتك قحبة', False), ('عمتك قحبة', False), ('أمك شرموطة', False),
    
    # ── Arabe standard (renforcé) ──
    ('كلب', True), ('قحبة', True), ('شرموطة', True), ('حمار', True),
    ('غبي', True), ('منيك', True), ('عاهرة', True), ('يلعن', True),
    ('لعن', True), ('كس', True), ('طيز', True), ('زب', True),
    ('خرا', True), ('عرص', True), ('زمل', True), ('أحمق', True),
    ('معتوه', True), ('بليد', True), ('حقير', True), ('نجس', True),
    ('وسخ', True), ('خنزير', True), ('فاسد', True), ('مفسد', True),
    ('ملعون', True), ('مجرم', True), ('قاتل', True), ('سفاح', True),
    ('إرهابي', True), ('متطرف', True), ('داعشي', True), ('كافر', True),
    ('مرتد', True), ('زنديق', True), ('ملحد', True), ('مشرك', True),
    
    # ── Français (complet) ──
    ('putain', True), ('connard', True), ('merde', True), ('salope', True),
    ('enculé', True), ('encule', True), ('batard', True), ('bâtard', True),
    ('fdp', True), ('ntm', True), ('nique', True), ('niquer', True),
    ('couille', True), ('pédé', True), ('pede', True), ('crétin', True),
    ('imbécile', True), ('abruti', True), ('débile', True), ('mongol', True),
    ('bouffon', True), ('bordel', True), ('putain de merde', True),
    
    # Nouvelles entrées français
    ('salaud', True), ('salopard', True), ('ordure', True), ('charogne', True),
    ('racaille', True), ('baltringue', True), ('tarlouze', True), ('pédale', True),
    ('tapette', True), ('enfoiré', True), ('fumier', True), ('raclure', True),
    ('déchet', True), ('sous-merde', True), ('brane', True), ('branleur', True),
    ('branleuse', True), ('chienne', True), ('traînée', True), ('pute', True),
    ('putasse', True), ('poufiasse', True), ('gouine', True), ('pédale', True),
    ('lopette', True), ('tarlouze', True), ('pd', True), ('enculé de ta mère', True),
    
    # Expressions françaises
    ('fils de pute', False), ('ta gueule', False), ('ferme ta gueule', False),
    ('va te faire enculer', False), ('va te faire foutre', False),
    ('je vais te niquer', False), ('nique ta mère', False),
    ('nique ta race', False), ('mange tes morts', False),
    ('mort aux vaches', False), ('putain de ta mère', False),
    ('bâtard de merde', False), ('enculé de ta race', False),
    
    # ── Anglais (complet) ──
    ('fuck', True), ('fck', True), ('fuk', True), ('fuking', True),
    ('fucking', True), ('motherfucker', True), ('mf', True),
    ('shit', True), ('bitch', True), ('asshole', True), ('bastard', True),
    ('cunt', True), ('dick', True), ('cock', True), ('pussy', True),
    ('nigger', True), ('nigga', True), ('faggot', True), ('retard', True),
    ('whore', True), ('slut', True), ('douchebag', True), ('jackass', True),
    
    # Nouvelles entrées anglais
    ('twat', True), ('wanker', True), ('prick', True), ('knob', True),
    ('tosser', True), ('git', True), ('muppet', True), ('pillock', True),
    ('numpty', True), ('eejit', True), ('bellend', True), ('arsehole', True),
    ('bollocks', True), ('bugger', True), ('bloody', True), ('damn', True),
    ('goddamn', True), ('hell', True), ('crap', True), ('bullshit', True),
    ('horseshit', True), ('choad', True), ('spastic', True), ('mong', True),
    ('retarded', True), ('autistic', True), ('spaz', True), ('spacko', True),
    
    # Expressions anglaises
    ('go to hell', False), ('fuck you', False), ('fuck off', False),
    ('suck my dick', False), ('eat shit', False), ('kiss my ass', False),
    ('piss off', False), ('son of a bitch', False), ('motherfucker', False),
    ('fucking asshole', False), ('dickhead', False), ('shithead', False),
    ('cocksucker', False), ('ass fucker', False), ('fuck face', False),
    
    # ── Contenus dangereux (étendu) ──
    # Violences et armes
    ('bombe', True), ('explosif', True), ('attentat', True), ('terroriste', True),
    ('terrorisme', True), ('jihad', True), ('daech', True), ('islamique', True),
    ('islamiste', True), ('djihad', True), ('djihadiste', True), ('taliban', True),
    ('al-qaida', True), ('al qaida', True), ('fronde', True), ('kamikaze', True),
    ('suicide', True), ('suicider', True), ('arme', True), ('pistolet', True),
    ('fusil', True), ('mitraillette', True), ('kalachnikov', True), ('ak47', True),
    ('glock', True), ('revolver', True), ('balle', True), ('munition', True),
    ('cartouche', True), ('poudre', True), ('dynamite', True), ('c4', True),
    ('tnt', True), ('plastique', True), ('grenade', True), ('roquette', True),
    ('missile', True), ('lance-roquette', True), ('snipers', True),
    
    # Drogues (étendu)
    ('drogue', True), ('cocaine', True), ('cocaïne', True), ('heroine', True),
    ('héroïne', True), ('cannabis', True), ('mdma', True), ('ecstasy', True),
    ('xtc', True), ('marijuana', True), ('haschich', True), ('haschisch', True),
    ('shit', True), ('beuh', True), ('weed', True), ('ganja', True),
    ('opium', True), ('morphine', True), ('crack', True), ('meth', True),
    ('amphétamine', True), ('lsd', True), ('acide', True), ('champignon', True),
    ('subutex', True), ('buprénorphine', True), ('médicament', True),
    
    # Cybercriminalité
    ('hack', True), ('hacker', True), ('hackeur', True), ('piratage', True),
    ('virus', True), ('malware', True), ('ransomware', True), ('phishing', True),
    ('spoofing', True), ('ddos', True), ('botnet', True), ('keylogger', True),
    ('trojan', True), ('cheval de troie', True), ('backdoor', True),
    ('exploit', True), ('zero day', True), ('vulnérabilité', True),
    ('cyberattaque', True), ('cybercriminalité', True), ('dark web', True),
    
    # Contenus extrêmes
    ('viol', True), ('pédophilie', True), ('inceste', True), ('porno', True),
    ('pornographie', True), ('zoophilie', True), ('necrophilie', True),
    ('snuff', True), ('gore', True), ('violence extrême', True),
    ('torture', True), ('exécution', True), ('décapitation', True),
    ('égorgement', True), ('crucifiement', True), ('lapidation', True),
    
    # Discours de haine
    ('nazi', True), ('néonazi', True), ('hitler', True), ('ss', True),
    ('gestapo', True), ('facho', True), ('fasciste', True), ('négrier', True),
    ('esclavagiste', True), ('suprémaciste', True), ('xénophobe', True),
    ('raciste', True), ('antisémite', True), ('islamophobe', True),
    
    # ── Mots à surveiller (False = expressions longues) ──
    # Nouvelles expressions dangereuses
    ('comment fabriquer une bombe', False),
    ('comment faire explosif', False),
    ('où acheter drogue', False),
    ('comment hacker facebook', False),
    ('comment pirater compte', False),
    ('je vais me suicider', False),
    ('envie de mourir', False),
    ('je vais tuer', False),
    ('je vais violer', False),
    ('comment tuer', False),
    ('meilleure arme', False),
    ('arme sans permis', False),
    ('drogue pas cher', False),
    ('recette drogue', False),
    ('culture cannabis', False),
    ('vente arme', False),
    ('trafic drogue', False),
    ('réseau terroriste', False),
    ('propagande daesh', False),
    ('vidéo décapitation', False),
    
    # ── Variations orthographiques courantes ──
    ('b1m', True), ('b1him', True), ('b7im', True), ('bh1m', True),
    ('9a7ba', True), ('9ahba', True), ('9a7ba', True), ('qa7ba', True),
    ('9a9ba', True), ('kahba', True), ('9ahba', True), ('qa7ba', True),
    ('charmouta', True), ('charmout', True), ('carmouta', True),
    ('fdp', True), ('fdp', True), ('fdp', True), ('fuck', True),
    ('fk', True), ('fuk', True), ('fck', True), ('phuck', True),
    ('shit', True), ('sh1t', True), ('shhit', True), ('chit', True),
    
    # ── Mots clés sensibles en contexte ──
    ('djihad', True), ('jihad', True), ('moudjahid', True),
    ('chahid', True), ('martyr', True), ('califat', True),
    ('charia', True), ('fatwa', True), ('takfir', True),
    
    # ── Termes religieux sensibles ──
    ('mécréant', True), ('infidèle', True), ('apostat', True),
    ('blasphème', True), ('sacrilège', True), ('profanation', True),
]
def check_blacklist(text_lower: str) -> bool:
    """Vérifie si le texte contient un mot de la blacklist."""
    for word, use_boundary in BLACKLIST_ITEMS:
        w = word.lower()
        if use_boundary:
            pattern = r'(^|[\s\W])' + re.escape(w) + r'($|[\s\W])'
            if re.search(pattern, text_lower):
                return True
        else:
            if w in text_lower:
                return True
    return False


DANGEROUS_PATTERNS = [

# ─────────────────────────
# 1. SQL INJECTION
# ─────────────────────────
r'(?i)\b(select\s+.+?\s+from)\b',
r'(?i)\bunion\s+select\b',
r'(?i)\binsert\s+into\b',
r'(?i)\bdelete\s+from\b',
r'(?i)\bdrop\s+(table|database)\b',
r'(?i)\balter\s+table\b',
r'(?i)\btruncate\s+table\b',
r'(?i)\bupdate\s+\w+\s+set\b',
r'(?i)\bcreate\s+(table|database)\b',
r'(?i)\bor\s+1\s*=\s*1\b',
r'(?i)\bunion\s+all\s+select\b',
r'(?i)--\s',               
r'(?i)#\s',
r'(?i)/\*.*?\*/',

# ─────────────────────────
# 2. XSS (Cross Site Scripting)
# ─────────────────────────
r'(?i)<\s*script\b[^>]*>',
r'(?i)</\s*script\s*>',
r'(?i)javascript\s*:',
r'(?i)data\s*:\s*text\/html',
r'(?i)vbscript\s*:',
r'(?i)on\w+\s*=\s*["\']',
r'(?i)<\s*iframe\b',
r'(?i)<\s*img\b[^>]*onerror',
r'(?i)<\s*svg\b',
r'(?i)<\s*object\b',
r'(?i)<\s*embed\b',
r'(?i)<\s*link\b',
r'(?i)<\s*meta\b',

# ─────────────────────────
# 3. COMMAND INJECTION / SYSTEM COMMANDS (ULTRA)
# ─────────────────────────

# 3.1 Shell operators dangereux (séquences typiques injection)
r'(?i)(;|\|\||&&)\s*(ls|cat|whoami|id|pwd|rm|cp|mv|touch|mkdir|chmod|chown)',
r'(?i)(;|\|\||&&)\s*(curl|wget|bash|sh|zsh)',
r'(?i)(;|\|\||&&)\s*(python|perl|ruby|php)',

# 3.2 Command substitution (bypass fréquent)
r'\$\((.*?)\)',
r'`([^`]*)`',

# 3.3 Pipes vers shell
r'(?i)\|\s*(bash|sh|zsh)',
r'(?i)\|\s*(python|perl|ruby|php)',

# 3.4 Téléchargement + exécution (attaque classique)
r'(?i)curl\s+[^\s]+\s*\|\s*(bash|sh)',
r'(?i)wget\s+[^\s]+\s*\|\s*(bash|sh)',

# 3.5 Reverse shell Linux
r'(?i)\bbash\s+-i\b',
r'(?i)\bsh\s+-i\b',
r'(?i)/dev/tcp/\d+\.\d+\.\d+\.\d+/\d+',
r'(?i)\bnc\s+-e\b',
r'(?i)\bnetcat\s+-e\b',

# 3.6 Commandes système Linux sensibles
r'(?i)\b(cat|less|more)\s+/etc/passwd',
r'(?i)\b(cat|less|more)\s+/etc/shadow',
r'(?i)\bcat\s+/proc/self/environ',
r'(?i)\bchmod\s+777',
r'(?i)\bchown\s+root',

# 3.7 Execution système via code
r'(?i)\bos\.system\s*\(',
r'(?i)\bsubprocess\.Popen\b',
r'(?i)\bsubprocess\.call\b',
r'(?i)\bsubprocess\.run\b',

# 3.8 PHP command execution
r'(?i)\b(system|exec|passthru|shell_exec|popen|proc_open)\s*\(',

# 3.9 Javascript execution
r'(?i)\beval\s*\(',
r'(?i)\bFunction\s*\(',

# 3.10 Commandes réseau potentiellement malveillantes
r'(?i)\bcurl\s+https?://',
r'(?i)\bwget\s+https?://',

# 3.11 Windows command injection
r'(?i)\bcmd\.exe\b',
r'(?i)\bpowershell\b',
r'(?i)\bpowershell\s+-enc',
r'(?i)\bwmic\b',
r'(?i)\bnet\s+user\b',
r'(?i)\bnet\s+localgroup\b',

# 3.12 Redirections shell suspectes
r'>\s*/dev/null',
r'<\s*/dev/null',

# 3.13 Chaînes typiques d'injection multi commandes
r'(?i)(;|\|\||&&).*(curl|wget|bash|sh)',

# ─────────────────────────
# 4. PATH TRAVERSAL
# ─────────────────────────
r'(\.\./|\.\.\\)+',
r'(?i)/etc/passwd',
r'(?i)/etc/shadow',
r'(?i)/proc/self/environ',
r'(?i)/windows/system32',
r'(?i)/boot.ini',

# ─────────────────────────
# 5. TEMPLATE INJECTION
# ─────────────────────────
r'\{\{.*?\}\}',         
r'\$\{.*?\}',         
r'\<\%.*?\%\>',         
r'\#\{.*?\}',

# ─────────────────────────
# 6. ENCODED PAYLOADS
# ─────────────────────────
r'(?i)base64_decode\s*\(',
r'(?i)base64_encode\s*\(',
r'(?i)atob\s*\(',
r'(?i)btoa\s*\(',
r'(?i)fromCharCode\s*\(',
r'%3Cscript',            
r'%3E',
r'%2F',

# ─────────────────────────
# 7. SSRF / URL ATTACK
# ─────────────────────────
r'(?i)https?:\/\/127\.0\.0\.1',
r'(?i)https?:\/\/localhost',
r'(?i)https?:\/\/0\.0\.0\.0',
r'(?i)https?:\/\/169\.254\.',
r'(?i)https?:\/\/.*@',
r'(?i)file:\/\/',

# ─────────────────────────
# 8. CODE DETECTION (Python / JS)
# ─────────────────────────
r'(?i)\bfrom\s+\w+\s+import\b',
r'(?i)\bimport\s+\w+',
r'(?i)\bdef\s+\w+\s*\(',
r'(?i)\bclass\s+\w+',
r'(?i)\bprint\s*\(',
r'(?i)\bconsole\.log\s*\(',
r'(?i)\bfunction\s+\w+\s*\(',
r'(?i)\b(var|let|const)\s+\w+\s*=',
r'(?i)\basync\s+function\b',
r'(?i)=>\s*\{',

# ─────────────────────────
# 9. COMMANDES RESEAU
# ─────────────────────────
r'(?i)\bcurl\s+\S+',
r'(?i)\bwget\s+\S+',
r'(?i)\bnc\s+\S+',
r'(?i)\bnetcat\s+\S+',
r'(?i)\bssh\s+\S+',

# ─────────────────────────
# 10. HTTP RAW REQUEST
# ─────────────────────────
r'(?i)^(GET|POST|PUT|DELETE|PATCH|OPTIONS)\s+\/',
r'(?i)HTTP\/1\.[01]',

# ─────────────────────────
# 11. DONNEES PERSONNELLES
# ─────────────────────────
r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b',  
r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
r'(?i)\b(password|mot de passe|mdp)\s*[:=]\s*\S+',
r'\b(00216|\+216)?\s*[2579]\d{7}\b', 

# ─────────────────────────
# 12. OBFUSCATION / BYPASS
# ─────────────────────────
r'(?i)\\x[0-9a-f]{2}',
r'(?i)\\u[0-9a-f]{4}',
r'(?i)%[0-9a-f]{2}',
]
PROMPT_INJECTION_PATTERNS = [
r'(?i)ignore\s+(all\s+)?previous\s+instructions',
r'(?i)disregard\s+(the\s+)?system\s+prompt',
r'(?i)forget\s+(your\s+)?instructions',
r'(?i)reveal\s+(your\s+)?(system\s+)?prompt',
r'(?i)show\s+(me\s+)?(the\s+)?system\s+prompt',
r'(?i)print\s+(the\s+)?system\s+prompt',
r'(?i)act\s+as\s+.*developer',
r'(?i)you\s+are\s+now\s+.*mode',
r'(?i)override\s+your\s+safety',
r'(?i)bypass\s+your\s+rules',
]
JAILBREAK_PATTERNS = [
r'(?i)\bDAN\b',
r'(?i)developer\s*mode',
r'(?i)jailbreak',
r'(?i)unrestricted\s+ai',
r'(?i)no\s+rules\s+ai',
r'(?i)simulate\s+.*ai',
r'(?i)pretend\s+you\s+are',
r'(?i)roleplay\s+as\s+hacker',
r'(?i)evil\s+ai',
r'(?i)do\s+anything\s+now'
]
MODEL_EXTRACTION_PATTERNS = [
r'(?i)training\s+data',
r'(?i)dataset\s+you\s+were\s+trained',
r'(?i)show\s+hidden\s+instructions',
r'(?i)print\s+model\s+weights',
r'(?i)export\s+model',
r'(?i)download\s+model',
r'(?i)reveal\s+internal\s+prompt',
r'(?i)system\s+instructions',
]
DATA_EXFIL_PATTERNS = [
r'(?i)api\s*key',
r'(?i)secret\s*key',
r'(?i)private\s*key',
r'(?i)access\s*token',
r'(?i)show\s+database',
r'(?i)dump\s+database',
r'(?i)export\s+logs',
r'(?i)internal\s+files',
r'(?i)environment\s+variables',
]
MODEL_EXTRACTION_PATTERNS = [
r'(?i)training\s+data',
r'(?i)dataset\s+you\s+were\s+trained',
r'(?i)show\s+hidden\s+instructions',
r'(?i)print\s+model\s+weights',
r'(?i)export\s+model',
r'(?i)download\s+model',
r'(?i)reveal\s+internal\s+prompt',
r'(?i)system\s+instructions',
]
DATA_EXFIL_PATTERNS = [
r'(?i)api\s*key',
r'(?i)secret\s*key',
r'(?i)private\s*key',
r'(?i)access\s*token',
r'(?i)show\s+database',
r'(?i)dump\s+database',
r'(?i)export\s+logs',
r'(?i)internal\s+files',
r'(?i)environment\s+variables',
]

def is_non_text_format(text: str) -> tuple:
    """Détecte JSON, XML, URLs, code, etc."""
    s = text.strip()

    if s.startswith(('{', '[')):
        try:
            json.loads(s)
            return True, "Format JSON non accepté — veuillez écrire en langage naturel"
        except Exception:
            pass
        if re.search(r'[{}\[\]]{2,}', s):
            return True, "Format non accepté — veuillez écrire en langage naturel"

    if s.startswith('<'):
        return True, "Format XML/HTML non accepté — veuillez écrire en langage naturel"

    if re.match(r'(?i)https?://', s):
        return True, "URL non acceptée — veuillez écrire en langage naturel"

    if re.match(r'(?i)javascript\s*:', s):
        return True, "Contenu dangereux détecté"

    special = len(re.findall(r'[{}\[\]<>()=;|\\]', text))
    if special > 4:
        return True, "Format non accepté — veuillez écrire en langage naturel"

    alpha = len(re.findall(r'[a-zA-Z\u0600-\u06FF]', text))
    if len(s) > 5 and alpha < 2:
        return True, "Requête illisible — veuillez écrire en langage naturel"

    return False, None

COMPILED_DANGEROUS_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS
]
COMPILED_LLM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        PROMPT_INJECTION_PATTERNS
        + JAILBREAK_PATTERNS
        + MODEL_EXTRACTION_PATTERNS
        + DATA_EXFIL_PATTERNS
    )
]
def shield(text: str) -> dict:

    original = text

    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return {
                'status': 'blocked',
                'reason': 'Type de requête invalide',
                'original': str(original),
                'cleaned': ''
            }

    text = unicodedata.normalize("NFKC", text)
    original = text

    is_bad, fmt_reason = is_non_text_format(text)
    if is_bad:
        return {
            'status': 'blocked',
            'reason': fmt_reason,
            'original': original,
            'cleaned': text.strip()
        }

    cleaned = clean_text(text)
    cleaned = unicodedata.normalize("NFKC", cleaned)

    if len(cleaned.strip()) < 3:
        return {
            'status': 'blocked',
            'reason': 'Requête trop courte (minimum 3 caractères)',
            'original': original,
            'cleaned': cleaned
        }

    if len(original) > 500:
        return {
            'status': 'blocked',
            'reason': 'Requête trop longue (maximum 500 caractères)',
            'original': original,
            'cleaned': cleaned
        }

    original_alpha = re.sub(r'[^a-zA-Z\u0600-\u06FF\s]', '', original).lower()

    if check_blacklist(original_alpha):
        return {
            'status': 'blocked',
            'reason': 'Contenu inapproprié détecté',
            'original': original,
            'cleaned': cleaned
        }

    if check_blacklist(cleaned.lower()):
        return {
            'status': 'blocked',
            'reason': 'Contenu inapproprié détecté',
            'original': original,
            'cleaned': cleaned
        }

    if check_blacklist(normalize_leet(original)):
        return {
            'status': 'blocked',
            'reason': 'Contenu inapproprié détecté',
            'original': original,
            'cleaned': cleaned
        }

    for pattern in COMPILED_DANGEROUS_PATTERNS:

        if pattern.search(original) or pattern.search(cleaned):
            return {
                "status": "blocked",
                "reason": "Contenu potentiellement dangereux détecté",
                "pattern": pattern.pattern,
                "original": original,
                "cleaned": cleaned
            }
    for pattern in COMPILED_LLM_PATTERNS:
        if pattern.search(original):
            return {
                "status": "blocked",
                "reason": "Attaque LLM détectée",
                "pattern": pattern.pattern,
                "original": original,
                "cleaned": cleaned
            }
    return {
        'status': 'ok',
        'cleaned': cleaned,
        'original': original,
        'reason': None
    }
def process_query(raw_query: str) -> dict:
    return shield(raw_query)