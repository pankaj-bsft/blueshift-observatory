import re
from email import message_from_string
from bs4 import BeautifulSoup

SPAM_TRIGGERS = [
    "free","winner","won","prize","claim","urgent","act now","limited time",
    "guaranteed","no risk","cash","money back","earn money","make money",
    "extra income","work from home","financial freedom","credit card",
    "no credit check","loan","debt","buy now","order now","click here",
    "call now","apply now","lose weight","weight loss","miracle","cure",
    "cheap","discount","save big","special offer","best price","lowest price",
    "percent off","congratulations","you have been selected","you are a winner",
    "dear friend","dear member","risk free","casino","gambling","lottery",
]

URL_SHORTENERS = ["bit.ly","tinyurl.com","goo.gl","t.co","ow.ly","buff.ly","short.link","tiny.cc","is.gd","cutt.ly"]
SUSPICIOUS_TLDS = [".xyz",".top",".click",".link",".online",".site",".win",".loan",".download"]

CLIENT_COMPAT = {
    "Gmail":        {"flexbox":True,"css_grid":True,"float":True,"media_queries":True},
    "Outlook":      {"flexbox":False,"css_grid":False,"float":False,"media_queries":False},
    "Apple Mail":   {"flexbox":True,"css_grid":True,"float":True,"media_queries":True},
    "Yahoo Mail":   {"flexbox":True,"css_grid":False,"float":True,"media_queries":True},
    "Thunderbird":  {"flexbox":True,"css_grid":True,"float":True,"media_queries":True},
    "Samsung Mail": {"flexbox":True,"css_grid":False,"float":True,"media_queries":True},
}


def _chk(name, status, detail, fix=None):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def analyze_subject(subject):
    if not subject:
        return {"subject": "", "checks": [], "issues": 0}
    checks = []
    issues = 0

    length = len(subject)
    if length > 60:
        checks.append(_chk("Subject Length", "warn", str(length) + " characters — ideal is 30-60.", "Shorten subject line to under 60 characters."))
        issues += 1
    else:
        checks.append(_chk("Subject Length", "pass", str(length) + " characters — good length."))

    caps = re.findall(r'\b[A-Z]{3,}\b', subject)
    if caps:
        checks.append(_chk("ALL CAPS Words", "warn", "ALL CAPS found: " + ", ".join(caps[:5]), "Avoid all-caps in subject lines."))
        issues += 1
    else:
        checks.append(_chk("ALL CAPS Words", "pass", "No excessive all-caps found."))

    exc = subject.count("!")
    if exc > 1:
        checks.append(_chk("Exclamation Marks", "warn", str(exc) + " exclamation mark(s).", "Use at most 1 exclamation mark."))
        issues += 1
    else:
        checks.append(_chk("Exclamation Marks", "pass", "Acceptable exclamation usage."))

    found = [t for t in SPAM_TRIGGERS if t in subject.lower()]
    if found:
        checks.append(_chk("Spam Words in Subject", "fail", "Spam words: " + ", ".join(found[:5]), "Remove spam trigger words."))
        issues += 1
    else:
        checks.append(_chk("Spam Words in Subject", "pass", "No spam trigger words in subject."))

    qmarks = subject.count("?")
    if qmarks > 2:
        checks.append(_chk("Question Marks", "warn", str(qmarks) + " question marks.", "Reduce question marks."))
        issues += 1
    else:
        checks.append(_chk("Question Marks", "pass", "Acceptable question mark usage."))

    return {"subject": subject, "checks": checks, "issues": issues}


def analyze_html(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    html_size_kb = round(len(html.encode("utf-8")) / 1024, 1)
    images = soup.find_all("img")
    links = soup.find_all("a", href=True)
    tables = soup.find_all("table")
    inline_styles = soup.find_all(style=True)
    link_urls = [a.get("href", "") for a in links]

    plain_text = re.sub(r'<[^>]+>', '', html)
    text_ratio = round((len(text) / max(len(plain_text), 1)) * 100)

    has_doctype = "<!doctype" in html.lower()
    charset_tag = bool(soup.find("meta", attrs={"charset": True}))
    viewport_tag = bool(soup.find("meta", attrs={"name": re.compile("viewport", re.I)}))

    max_width = None
    width_ok = False
    for el in [soup.find("body"), soup.find("table")]:
        if el:
            w = re.search(r'max-width\s*:\s*(\d+)px', el.get("style", ""))
            if w:
                max_width = int(w.group(1))
                width_ok = 500 <= max_width <= 680
                break

    text_lower = text.lower()
    found_triggers = [t for t in SPAM_TRIGGERS if t in text_lower]
    caps_words = [w for w in re.findall(r'\b[A-Z]{3,}\b', text) if w not in ("HTML","URL","CSS","TLS","SPF","DKIM","UTF")]
    excl = html.count("!")
    dollars = html.count("$")
    shorteners = [u for u in link_urls if any(s in u for s in URL_SHORTENERS)]
    susp_tlds = [u for u in link_urls if any(t in u for t in SUSPICIOUS_TLDS)]

    all_css = " ".join([el.get("style", "") for el in soup.find_all(style=True)])
    for s in soup.find_all("style"):
        if s.string:
            all_css += " " + s.string

    has_flex = "flex" in all_css.lower()
    has_grid = "grid" in all_css.lower()
    has_float = "float" in all_css.lower()
    has_mq = "@media" in all_css
    important_count = all_css.count("!important")
    ext_fonts = any("font" in (l.get("href", "").lower()) for l in soup.find_all("link"))

    imgs_no_alt = [i for i in images if not i.get("alt")]
    small_fonts = 0
    for el in soup.find_all(style=True):
        m = re.search(r'font-size\s*:\s*(\d+)px', el.get("style", ""))
        if m and int(m.group(1)) < 11:
            small_fonts += 1

    html_tag = soup.find("html")
    has_lang = bool(html_tag and html_tag.get("lang"))
    white_text = len(soup.find_all(style=re.compile(r'color\s*:\s*(#fff|#ffffff|white)', re.I))) > 0

    structure = [
        _chk("DOCTYPE Declaration", "pass" if has_doctype else "fail",
             "DOCTYPE is present." if has_doctype else "Missing DOCTYPE.",
             None if has_doctype else "Add <!DOCTYPE html> at top."),
        _chk("Charset Meta Tag", "pass" if charset_tag else "warn",
             "Character encoding declared." if charset_tag else "No charset meta tag.",
             None if charset_tag else "Add meta charset=UTF-8."),
        _chk("Viewport Meta Tag", "pass" if viewport_tag else "warn",
             "Responsive viewport present." if viewport_tag else "No viewport meta tag.",
             None if viewport_tag else "Add viewport meta tag."),
        _chk("Email Width", "pass" if width_ok else "warn",
             ("Max-width " + str(max_width) + "px — ideal 500-680px.") if max_width else "No max-width detected.",
             None if width_ok else "Set max-width between 500px and 680px."),
        _chk("Table-Based Layout", "pass" if tables else "warn",
             str(len(tables)) + " table(s) found." if tables else "No tables found.",
             None if tables else "Use table-based layout for Outlook compatibility."),
        _chk("Inline CSS", "pass" if inline_styles else "warn",
             str(len(inline_styles)) + " inline styles found." if inline_styles else "No inline styles.",
             None if inline_styles else "Inline your CSS styles."),
    ]

    spam = [
        _chk("Spam Trigger Words", "pass" if not found_triggers else "fail",
             "No spam triggers detected." if not found_triggers else "Spam words: " + ", ".join(found_triggers[:5]),
             None if not found_triggers else "Remove spam trigger words."),
        _chk("Excessive CAPS", "pass" if len(caps_words) < 3 else "warn",
             "No excessive caps." if len(caps_words) < 3 else "CAPS words: " + ", ".join(caps_words[:5]),
             None if len(caps_words) < 3 else "Avoid excessive capitalization."),
        _chk("Exclamation Marks", "pass" if excl <= 3 else "warn",
             str(excl) + " exclamation mark(s).", None if excl <= 3 else "Reduce to 1-2 max."),
        _chk("Dollar Signs", "pass" if dollars <= 2 else "warn",
             str(dollars) + " dollar sign(s).", None if dollars <= 2 else "Reduce monetary references."),
        _chk("Low Text Ratio", "pass" if text_ratio >= 20 else "warn",
             str(text_ratio) + "% readable text.",
             None if text_ratio >= 20 else "Add more plain text content."),
        _chk("Link Domains", "pass" if not susp_tlds else "warn",
             str(len(link_urls)) + " link(s) checked — no suspicious TLDs." if not susp_tlds else "Suspicious: " + ", ".join(susp_tlds[:3]),
             None if not susp_tlds else "Avoid suspicious TLD links."),
        _chk("URL Shorteners", "pass" if not shorteners else "fail",
             "No URL shorteners." if not shorteners else "Shorteners: " + ", ".join(shorteners[:3]),
             None if not shorteners else "Replace with full URLs."),
    ]

    accessibility = [
        _chk("Image Alt Text", "pass" if not imgs_no_alt else "warn",
             "All images have alt." if not imgs_no_alt else str(len(imgs_no_alt)) + " image(s) missing alt.",
             None if not imgs_no_alt else "Add alt text to all images."),
        _chk("Small Font Sizes", "pass" if small_fonts == 0 else "warn",
             "No small fonts detected." if small_fonts == 0 else str(small_fonts) + " font(s) smaller than 11px.",
             None if small_fonts == 0 else "Use minimum 13px for body text."),
        _chk("Color Contrast", "warn" if white_text else "pass",
             "Potential white-on-white text." if white_text else "No obvious contrast issues.",
             "Fix text/background contrast." if white_text else None),
        _chk("Language Attribute", "pass" if has_lang else "info",
             "lang attribute present." if has_lang else "No lang attribute on html tag.",
             None if has_lang else "Add lang=en to html tag."),
    ]

    css = [
        _chk("No Flexbox", "pass" if not has_flex else "warn",
             "No flexbox — good for Outlook." if not has_flex else "Flexbox detected — may break in Outlook.",
             None if not has_flex else "Replace flexbox with table layout."),
        _chk("No CSS Grid", "pass" if not has_grid else "warn",
             "No CSS Grid." if not has_grid else "CSS Grid — limited email support.",
             None if not has_grid else "Replace with table layout."),
        _chk("Float Layout", "warn" if has_float else "pass",
             "Float CSS detected." if has_float else "No float layout.",
             "Replace floats with table columns." if has_float else None),
        _chk("Font Stack", "warn" if ext_fonts else "pass",
             "External fonts detected." if ext_fonts else "No external fonts — good.",
             "Use web-safe fallback fonts." if ext_fonts else None),
        _chk("Excessive !important", "info" if important_count > 50 else "pass",
             str(important_count) + " !important declaration(s).",
             "Reduce !important usage." if important_count > 50 else None),
        _chk("Media Queries", "pass" if has_mq else "info",
             "Media queries found — responsive." if has_mq else "No media queries — may not be mobile responsive.",
             None if has_mq else "Add media queries for mobile."),
    ]

    detected = {"flexbox": has_flex, "css_grid": has_grid, "float": has_float, "media_queries": has_mq}
    client_compat = {}
    for client, support in CLIENT_COMPAT.items():
        issues = [f.replace("_", " ") for f, used in detected.items() if used and not support[f]]
        client_compat[client] = {
            "status": "pass" if not issues else "warn",
            "issues": issues,
            "detail": "Compatible" if not issues else str(len(issues)) + " issue(s): " + ", ".join(issues),
        }

    all_checks = structure + spam + accessibility + css
    errors = sum(1 for c in all_checks if c["status"] == "fail")
    warnings = sum(1 for c in all_checks if c["status"] in ("warn", "info"))
    passed = sum(1 for c in all_checks if c["status"] == "pass")
    score = max(0, 100 - errors * 15 - warnings * 5)
    grade = "Excellent" if score >= 90 else "Good" if score >= 75 else "Fair" if score >= 50 else "Poor"

    return {
        "score": score, "grade": grade,
        "errors": errors, "warnings": warnings, "passed": passed,
        "stats": {
            "html_size_kb": html_size_kb,
            "images": len(images),
            "links": len(link_urls),
            "tables": len(tables),
            "inline_styles": len(inline_styles),
            "text_ratio": text_ratio,
        },
        "structure": structure,
        "spam": spam,
        "accessibility": accessibility,
        "css": css,
        "client_compat": client_compat,
        "html_preview": html,
    }


def analyze_headers(mime_content):
    msg = message_from_string(mime_content)
    from_h      = msg.get("From", "")
    subject     = msg.get("Subject", "")
    msg_id      = msg.get("Message-ID", "")
    date        = msg.get("Date", "")
    reply_to    = msg.get("Reply-To", "")
    list_unsub  = msg.get("List-Unsubscribe", "")
    feedback_id = msg.get("Feedback-ID", "")

    from_m = re.search(r'<([^>]+)>', from_h) or re.search(r'([\w.+-]+@[\w.-]+)', from_h)
    from_email = from_m.group(1) if from_m else from_h
    from_domain = from_email.split("@")[-1].lower() if "@" in from_email else ""
    parent_domain = ".".join(from_domain.split(".")[-2:]) if from_domain else ""

    auth_results = msg.get_all("Authentication-Results", []) or []
    arc_auth_results = msg.get_all("ARC-Authentication-Results", []) or []
    received_spf = msg.get_all("Received-SPF", []) or []
    dkim_signatures = msg.get_all("DKIM-Signature", []) or []

    auth_text = " ".join(auth_results + arc_auth_results)
    raw_text = "\n".join([
        auth_text,
        " ".join(received_spf),
        " ".join(dkim_signatures),
        mime_content,
    ])

    def _extract_status(text, key):
        patterns = [
            rf'\b{re.escape(key)}\s*=\s*([a-z0-9_-]+)',
            rf'\b{re.escape(key)}\s*:\s*([a-z0-9_-]+)',
            rf'\b{re.escape(key)}\s+([a-z0-9_-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).lower()
        return ""

    def _extract_dkim_domain(text):
        for pattern in [
            r'\bheader\.d=([\w.-]+)',
            r'\bd=([\w.-]+)',
        ]:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).lower()
        return ""

    spf = _extract_status(auth_text, "spf")
    dkim = _extract_status(auth_text, "dkim")
    dmarc = _extract_status(auth_text, "dmarc")
    arc = _extract_status(auth_text, "arc")
    dkim_domain = _extract_dkim_domain(auth_text)

    raw_lower = raw_text.lower()

    if not spf:
        received_spf_text = " ".join(received_spf).lower()
        if re.search(r'\bpass\b', received_spf_text):
            spf = "pass"
        elif re.search(r'\bsoftfail\b', received_spf_text):
            spf = "softfail"
        elif re.search(r'\bfail\b', received_spf_text):
            spf = "fail"
        elif re.search(r'\bneutral\b', received_spf_text):
            spf = "neutral"
        elif re.search(r'\btemperror\b', received_spf_text):
            spf = "temperror"
        elif re.search(r'\bpermerror\b', received_spf_text):
            spf = "permerror"
        elif "spf" in received_spf_text:
            spf = "present"

    if not dkim:
        if dkim_signatures:
            dkim = "present"
        else:
            dkim_sig_text = " ".join(dkim_signatures).lower()
            if "dkim=pass" in dkim_sig_text:
                dkim = "pass"
            elif "dkim=fail" in dkim_sig_text:
                dkim = "fail"

    if not dkim_domain and dkim_signatures:
        dkim_domain = _extract_dkim_domain(" ".join(dkim_signatures))

    if not dmarc:
        auth_lower = auth_text.lower()
        if re.search(r'\bdmarc\s*=\s*pass\b', auth_lower) or re.search(r'\bdmarc\s+pass\b', auth_lower):
            dmarc = "pass"
        elif re.search(r'\bdmarc\s*=\s*fail\b', auth_lower) or re.search(r'\bdmarc\s+fail\b', auth_lower):
            dmarc = "fail"
        elif re.search(r'\bdmarc\s*=\s*quarantine\b', auth_lower) or re.search(r'\bdmarc\s+quarantine\b', auth_lower):
            dmarc = "quarantine"
        elif re.search(r'\bdmarc\s*=\s*reject\b', auth_lower) or re.search(r'\bdmarc\s+reject\b', auth_lower):
            dmarc = "reject"
        elif re.search(r'\bdmarc\s*=\s*none\b', auth_lower) or re.search(r'\bdmarc\s+none\b', auth_lower):
            dmarc = "none"
        elif "dmarc" in raw_lower:
            dmarc = "present"

    if not arc:
        arc_lower = auth_text.lower()
        if re.search(r'\barc\s*=\s*pass\b', arc_lower) or re.search(r'\bcv\s*=\s*pass\b', raw_lower):
            arc = "pass"
        elif re.search(r'\barc\s*=\s*fail\b', arc_lower) or re.search(r'\bcv\s*=\s*fail\b', raw_lower):
            arc = "fail"
        elif "arc" in raw_lower:
            arc = "present"

    if not dkim_domain:
        dkim_domain = _extract_dkim_domain(raw_text)

    dkim_aligned = bool(dkim_domain and from_domain and
                        (dkim_domain == from_domain or from_domain.endswith("." + dkim_domain)))

    rt_m = re.search(r'<([^>]+)>', reply_to) or re.search(r'([\w.+-]+@[\w.-]+)', reply_to)
    rt_email = rt_m.group(1) if rt_m else ""
    rt_mismatch = bool(rt_email and rt_email.split("@")[-1].lower() != from_domain)

    received = msg.get_all("Received") or []
    hops = []
    for r in received:
        tls = re.search(r'version=(TLS\S+)', r, re.I)
        ip  = re.search(r'\[([\d.]+)\]', r)
        hops.append({
            "raw": r[:200],
            "ip": ip.group(1) if ip else None,
            "tls": tls.group(1) if tls else None,
        })
    sending_ip = hops[-1]["ip"] if hops else None
    tls_used = any(h["tls"] for h in hops)

    html_body = ""
    text_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = payload.decode("utf-8", errors="ignore")
            if ct == "text/html":
                html_body = decoded
            elif ct == "text/plain":
                text_body = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode("utf-8", errors="ignore")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                text_body = decoded

    html_analysis = analyze_html(html_body) if html_body else None
    subject_analysis = analyze_subject(subject)

    header_checks = [
        _chk("SPF",  "pass" if spf == "pass" else "fail",  "SPF: " + spf),
        _chk("DKIM", "pass" if dkim == "pass" else "fail",
             "DKIM: " + dkim + (" (domain: " + dkim_domain + ")" if dkim_domain else "")),
        _chk("DMARC", "pass" if dmarc == "pass" else ("warn" if dmarc == "none" else "fail"),
             "DMARC: " + dmarc + " | From: " + from_domain + " | Parent: " + parent_domain),
        _chk("ARC",  "pass" if arc == "pass" else "info",  "ARC: " + arc),
        _chk("DKIM Alignment", "pass" if dkim_aligned else "warn",
             "From: " + from_domain + " | DKIM: " + dkim_domain if dkim_domain else "DKIM domain unknown."),
        _chk("Reply-To Match", "warn" if rt_mismatch else "pass",
             ("Reply-To mismatch: " + rt_email) if rt_mismatch else "Reply-To OK or not set."),
        _chk("Message-ID", "pass" if "@" in msg_id else "warn",
             msg_id[:80] if msg_id else "No Message-ID found."),
        _chk("List-Unsubscribe", "pass" if list_unsub else "warn",
             list_unsub[:80] if list_unsub else "No List-Unsubscribe header.",
             None if list_unsub else "Add List-Unsubscribe header."),
        _chk("Feedback-ID", "pass" if feedback_id else "info",
             ("Feedback-ID: " + feedback_id[:80]) if feedback_id else "No Feedback-ID header."),
        _chk("TLS Encryption", "pass" if tls_used else "warn",
             "TLS used in delivery chain." if tls_used else "No TLS detected in received headers."),
    ]

    return {
        "from": from_h,
        "from_domain": from_domain,
        "parent_domain": parent_domain,
        "subject": subject,
        "date": date,
        "message_id": msg_id,
        "sending_ip": sending_ip,
        "hops": hops,
        "auth": {"spf": spf, "dkim": dkim, "dmarc": dmarc, "arc": arc, "dkim_domain": dkim_domain},
        "auth_headers": {
            "authentication_results": auth_results,
            "arc_authentication_results": arc_auth_results,
            "received_spf": received_spf,
            "dkim_signature": dkim_signatures,
        },
        "header_checks": header_checks,
        "subject_analysis": subject_analysis,
        "html_analysis": html_analysis,
        "raw_headers": mime_content[:8000],
        "has_html": bool(html_body),
        "has_text": bool(text_body),
    }
