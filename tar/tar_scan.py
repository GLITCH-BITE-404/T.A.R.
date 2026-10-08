#!/usr/bin/env python3
"""
T.A.R. file safety check -- static only, NOTHING is ever executed.

Works the way VirusTotal's behaviour/capability tab reads a file: it parses the
program's real IMPORT TABLE (which Windows functions it calls), its strings and
its real file type, and reports what it is built to DO as MITRE ATT&CK
techniques (T1486 Data Encrypted for Impact, T1490 Inhibit System Recovery...),
each with the evidence that triggered it. Names never decide anything:
"rans0m.exe" can be a harmless hello-world and "ReadyOrNot.exe" the real
ransomware. Optional extras: a VirusTotal hash lookup (70+ engines; the file is
NEVER uploaded) when a VirusTotal key is saved, and ClamAV when installed.
"""
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

MAX_READ = 40 * 1024 * 1024        # bytes of a file we look into
MAX_FILES = 600

EXEC_EXT = {".exe", ".dll", ".scr", ".com", ".pif", ".msi", ".bat", ".cmd", ".ps1", ".vbs",
            ".vbe", ".js", ".jse", ".wsf", ".hta", ".jar", ".lnk", ".sh", ".py", ".elf",
            ".appimage", ".run", ".bin", ".apk"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".jpg", ".jpeg",
           ".png", ".gif", ".mp3", ".mp4", ".mkv", ".avi", ".zip", ".rar", ".odt"}
SCRIPT_EXT = {".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta", ".sh",
              ".py", ".pl", ".rb", ".php", ".lua", ".bash", ".zsh", ".fish"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".iso", ".cab"}

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

# ---- evidence vocab ----------------------------------------------------------
# imports are matched against the real import table (Windows programs); "str"
# needles against the file's strings (also how .NET and scripts show API use).
CRYPTO_IMP = {"cryptencrypt", "bcryptencrypt", "cryptgenkey", "cryptimportkey",
              "cryptderivekey", "bcryptgeneratesymmetrickey", "cryptacquirecontextw",
              "cryptacquirecontexta"}
CRYPTO_STR = ["aesmanaged", "rijndaelmanaged", "aescryptoserviceprovider", "createencryptor",
              "rsacryptoserviceprovider", "aes.create(", "fernet(", "from crypto.cipher",
              "cryptography.hazmat", "openssl enc -"]
WALK_IMP = {"findfirstfilew", "findfirstfilea", "findfirstfileexw", "findnextfilew",
            "findnextfilea", "getlogicaldrives", "getlogicaldrivestringsw"}
WALK_STR = ["directory.getfiles", "enumeratefiles", "getdirectories", "os.walk(",
            "glob.glob(", "find / -", "find ~ -", "get-childitem -recurse", "gci -r"]
NOTE_STR = ["your files have been encrypted", "your files are encrypted",
            "files have been encrypted", "your personal files are encrypted", "pay the ransom",
            "send bitcoin to", "decrypt your files", "to recover your files",
            "files will be permanently", "how_to_decrypt", "readme_for_decrypt",
            "restore-my-files", "decrypt-instructions", "your network has been penetrated",
            "price for decryption", "all of your files"]

# (id, technique, tactic, weight, kinds, evidence spec)
#   spec: imp_all / imp_any = import names; str_any = strings; need = other ids
# kinds: pe = Windows program, elf = Linux program, script = text code, other
ALL = ("pe", "elf", "script", "other")
WIN = ("pe", "script", "other")
CODE = ("script", "other")
TECHNIQUES = [
    # ---- Impact
    ("T1490", "Inhibit System Recovery (deletes backups/shadow copies)", "Impact", 5, ALL,
     {"str_any": ["vssadmin delete shadows", "vssadmin.exe delete", "shadowcopy delete",
                  "wbadmin delete catalog", "recoveryenabled no", "delete shadows /all",
                  "bootstatuspolicy ignoreallfailures", "win32_shadowcopy"]}),
    ("note", "Ransom note text", "Impact", 5, ALL, {"str_any": NOTE_STR}),
    ("T1485", "Data Destruction (deletes/wipes data)", "Impact", 4, CODE + ("pe",),
     {"str_any": ["rm -rf /", "rm -rf ~", "rm -rf $home", "--no-preserve-root", "format c:",
                  "del /s /q c:\\", "del /f /s /q", "cipher /w", "dd if=/dev/zero of=/dev/",
                  "dd if=/dev/urandom of=/dev/", ":(){ :|:& };:", "remove-item -recurse -force c:\\"]}),
    # ---- Defense evasion
    ("T1562.001", "Impair Defenses: disables antivirus/Defender", "Defense Evasion", 4, ALL,
     {"str_any": ["disablerealtimemonitoring", "add-mppreference -exclusion", "disableantispyware",
                  "sc stop windefend", "sc config windefend", "set-mppreference -disable"]}),
    ("T1055", "Process Injection (runs code inside other programs)", "Defense Evasion", 4, ("pe",),
     {"imp_all": ["writeprocessmemory"],
      "imp_any": ["createremotethread", "ntcreatethreadex", "queueuserapc", "setthreadcontext",
                  "rtlcreateuserthread"]}),
    ("T1027", "Obfuscated Files or Information (hides its real commands)", "Defense Evasion", 2,
     ALL, {"str_any": ["-encodedcommand", "powershell -enc", "powershell.exe -enc", "-e jab",
                       "frombase64string(", "eval(base64", "exec(base64", "eval(atob",
                       "base64 -d | sh", "base64 -d|sh", "exec(compile(", "string.fromcharcode",
                       "exec(zlib.decompress", "marshal.loads("]}),
    ("T1497", "Virtualization/Sandbox Evasion (checks if it's being analysed)", "Defense Evasion",
     1, WIN, {"str_any": ["vboxservice", "vmtoolsd", "sandboxie", "sbiedll.dll", "wine_get_unix_file_name"]}),
    ("T1070.004", "Indicator Removal: deletes itself after running", "Defense Evasion", 2, WIN,
     {"str_any": ["ping 127.0.0.1 -n", "choice /c y /n /d y /t"], "need_str": ["del "]}),
    # ---- Persistence
    ("T1547.001", "Boot/Logon Autostart: Registry Run key", "Persistence", 2, WIN,
     {"str_any": ["currentversion\\run", "winlogon\\shell", "winlogon\\userinit"]}),
    ("T1053.005", "Scheduled Task (starts itself on a timer)", "Persistence", 2, WIN,
     {"str_any": ["schtasks /create", "schtasks.exe /create", "register-scheduledtask"]}),
    ("T1543.003", "Creates a Windows service", "Persistence", 2, ("pe",),
     {"imp_any": ["createservicew", "createservicea"]}),
    ("T1037", "Autostart on Linux (cron/systemd/shell rc)", "Persistence", 2, CODE,
     {"str_any": ["crontab -", "/etc/rc.local", "systemctl enable", ".config/autostart",
                  ">> ~/.bashrc", ">>~/.bashrc", ">> ~/.profile", ".config/systemd/user"]}),
    # ---- Credential access / collection
    ("T1555.003", "Credentials from Web Browsers (saved passwords/cookies)", "Credential Access",
     3, WIN, {"str_any": ["\\login data", "\\local state", "logins.json", "key4.db",
                          "\\network\\cookies"], "imp_or_str": ["cryptunprotectdata"]}),
    ("T1056.001", "Keylogging (hooks the keyboard and reads keys)", "Collection", 3, WIN,
     {"imp_any": ["setwindowshookexw", "setwindowshookexa"],
      "imp_or_str": ["getasynckeystate", "getkeyboardstate", "getkeynametextw", "pynput.keyboard"]}),
    ("T1113", "Screen Capture", "Collection", 1, WIN,
     {"imp_all": ["bitblt", "getdc"], "str_any": ["screenshot", "copyfromscreen"]}),
    ("T1552.004", "Steals private keys (SSH/crypto wallets)", "Credential Access", 3, ALL,
     {"str_any": ["/.ssh/id_rsa", "/.ssh/id_ed25519", "wallet.dat", "exodus\\exodus.wallet",
                  "\\electrum\\wallets", "metamask"]}),
    # ---- Execution
    ("T1059.001", "PowerShell run hidden / with policy bypass", "Execution", 2, ALL,
     {"str_any": ["-windowstyle hidden", "-w hidden", "-executionpolicy bypass", "-ep bypass",
                  "-exec bypass", "-nop -w"]}),
    # ---- Command & control / exfiltration
    ("T1105", "Ingress Tool Transfer (downloads and runs more code)", "Command and Control", 2, ALL,
     {"imp_or_str": ["urldownloadtofilew", "urldownloadtofilea", "certutil -urlcache",
                     "bitsadmin /transfer", "downloadstring(", "| sh", "| bash", "|sh", "|bash",
                     "iex(", "iex (", "invoke-expression", "start-bitstransfer"]}),
    ("T1567", "Exfiltration to web service (Discord/Telegram webhooks)", "Exfiltration", 3, ALL,
     {"str_any": ["discord.com/api/webhooks", "discordapp.com/api/webhooks",
                  "api.telegram.org/bot", "pastebin.com/raw"]}),
    ("T1090.003", "Uses Tor (.onion) -- typical for ransom/payment or C2", "Command and Control",
     2, ALL, {"str_any": [".onion/", ".onion ", ".onion\n", "tor2web"]}),
    # ---- Lateral movement
    ("T1091", "Replication Through Removable Media (spreads via USB)", "Lateral Movement", 3, WIN,
     {"str_any": ["[autorun]", "autorun.inf"], "imp_any": ["getdrivetypew", "getdrivetypea"]}),
    ("T1021.002", "SMB/Admin shares (spreads over the network)", "Lateral Movement", 2, WIN,
     {"str_any": ["\\admin$", "\\c$", "psexec", "net use \\\\"]}),
    ("macro", "Office macro that runs on open", "Execution", 2, ("other",),
     {"str_any": ["autoopen", "document_open", "workbook_open", "auto_open"],
      "need_str": ["vbaproject"]}),
    ("pdf", "PDF that runs JavaScript or launches programs", "Execution", 2, ("other",),
     {"str_any": ["/javascript", "/launch", "/openaction", "/embeddedfile"], "need_magic": b"%PDF"}),
]
# capabilities shown as "info" (normal programs do these too) -- like VT's capa list
INFO = [
    ("encrypts data", CRYPTO_IMP, CRYPTO_STR),
    ("lists files and folders (T1083)", WALK_IMP, WALK_STR),
    ("talks to the internet", {"internetopenw", "internetopena", "winhttpopen", "wsastartup",
                               "internetconnectw", "httpsendrequestw"},
     ["httpclient", "webclient", "requests.get(", "urllib.request", "curl ", "wget "]),
    ("starts other programs", {"createprocessw", "createprocessa", "shellexecutew", "shellexecuteexw",
                               "winexec"}, ["process.start(", "subprocess.", "os.system("]),
    ("changes the registry", {"regsetvalueexw", "regsetvalueexa", "regcreatekeyexw"}, ["registry.setvalue"]),
    ("changes the wallpaper", {"systemparametersinfow"}, ["spi_setdeskwallpaper"]),
    ("checks for a debugger", {"isdebuggerpresent", "checkremotedebuggerpresent"}, []),
]


def _strings(data):
    """Printable ASCII and UTF-16 runs -- what the program carries as text."""
    asc = re.findall(rb"[\x20-\x7e]{5,}", data)
    wide = re.findall(rb"(?:[\x20-\x7e]\x00){5,}", data)
    return [s.decode("ascii", "replace") for s in asc] + \
           [s.decode("utf-16le", "replace") for s in wide]


def _entropy(data):
    if not data:
        return 0.0
    n = min(len(data), 4_000_000)
    counts = [0] * 256
    for b in data[:n]:
        counts[b] += 1
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def _pe_info(data):
    """Parse a PE: sections (+entropy), IMPORT TABLE, .NET, signature. None if not PE."""
    try:
        if data[:2] != b"MZ":
            return None
        off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[off:off + 4] != b"PE\0\0":
            return None
        nsec = struct.unpack_from("<H", data, off + 6)[0]
        opt_size = struct.unpack_from("<H", data, off + 20)[0]
        opt = off + 24
        pe64 = struct.unpack_from("<H", data, opt)[0] == 0x20b
        dd = opt + (112 if pe64 else 96)
        imp_rva = struct.unpack_from("<I", data, dd + 8)[0]          # entry 1 = imports
        sec_size = struct.unpack_from("<I", data, dd + 4 * 8 + 4)[0]  # entry 4 = security
        clr_size = struct.unpack_from("<I", data, dd + 14 * 8 + 4)[0]  # entry 14 = .NET
        secs, table = [], []
        st = opt + opt_size
        for i in range(min(nsec, 40)):
            e = st + i * 40
            name = data[e:e + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, e + 8)
            secs.append((name, _entropy(data[raw_ptr:raw_ptr + raw_size])))
            table.append((va, max(vsize, raw_size), raw_ptr))

        def at(rva):
            for va, size, ptr in table:
                if va <= rva < va + size:
                    return rva - va + ptr
            return None

        def cstr(o):
            return data[o:data.index(b"\0", o)].decode("ascii", "replace") if o is not None else ""

        imports = {}
        d = at(imp_rva) if imp_rva else None
        while d is not None and d + 20 <= len(data) and len(imports) < 200:
            oft, _t, _f, name_rva, ft = struct.unpack_from("<IIIII", data, d)
            if not name_rva:
                break
            dll = cstr(at(name_rva)).lower()
            funcs = []
            t = at(oft or ft)
            step, high = (8, 1 << 63) if pe64 else (4, 1 << 31)
            while t is not None and t + step <= len(data) and len(funcs) < 2000:
                v = struct.unpack_from("<Q" if pe64 else "<I", data, t)[0]
                if not v:
                    break
                if not v & high:
                    funcs.append(cstr((at(v & 0x7fffffff) or 0) + 2).lower())
                t += step
            imports[dll] = funcs
            d += 20
        return {"sections": secs, "signed": sec_size > 0, "dotnet": clr_size > 0,
                "x64": pe64, "imports": imports}
    except (struct.error, IndexError, ValueError):
        return {"sections": [], "signed": False, "dotnet": False, "imports": {}, "broken": True}


def _ftype(path):
    try:
        return subprocess.run(["file", "-b", "--", path], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def analyze(path, shown=None):
    """One file -> {name, type, sha256, score, level, techniques, info, findings}."""
    shown = shown or os.path.basename(path)
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        data = f.read(MAX_READ)
        h.update(data)
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    base = {"name": shown, "path": path, "sha256": h.hexdigest(), "size": st.st_size}
    if EICAR in data:
        return dict(base, type="EICAR antivirus test file", score=0, level="test file",
                    techniques=[], info=[],
                    findings=[("the standard harmless antivirus TEST file (EICAR) -- every "
                               "scanner flags it on purpose; it can't do anything", "")])
    ftype = _ftype(path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    is_script = ext in SCRIPT_EXT or "script" in ftype.lower()
    cat = ("pe" if data[:2] == b"MZ" else "elf" if data[:4] == b"\x7fELF" else
           "script" if is_script else "other")
    pe = _pe_info(data) if cat == "pe" else None
    imps = {f for fs in (pe or {}).get("imports", {}).values() for f in fs}
    strs = _strings(data)
    text = "\n".join(strs).lower()
    techs, findings, score = [], [], 0

    def tech(tid, what, tactic, w, ev):
        nonlocal score
        score += w
        techs.append({"id": tid, "what": what, "tactic": tactic, "evidence": ev[:120]})

    # Masquerading (T1036): the only place a NAME matters -- when it lies
    stem2 = os.path.splitext(os.path.splitext(name)[0])[1].lower()
    if "\u202e" in name:
        tech("T1036.002", "Masquerading: hidden right-to-left trick fakes the extension",
             "Defense Evasion", 5, repr(name))
    elif stem2 in DOC_EXT and ext in EXEC_EXT:
        tech("T1036.007", "Masquerading: double extension (%s%s)" % (stem2, ext),
             "Defense Evasion", 4, name)
    if cat in ("pe", "elf") and ext in DOC_EXT:
        tech("T1036.008", "Masquerading: says %s but is really a program" % ext,
             "Defense Evasion", 5, ftype[:60])

    hit = set()
    for tid, what, tactic, w, kinds, spec in TECHNIQUES:
        if cat not in kinds:
            continue
        if "need_magic" in spec and not data.startswith(spec["need_magic"]):
            continue
        if "need_str" in spec and not any(n in text for n in spec["need_str"]):
            continue
        # every evidence group the rule lists must match (AND); inside a group, any one
        ev, ok = [], True
        if "imp_all" in spec:
            ok = all(i in imps for i in spec["imp_all"])
            ev += spec["imp_all"]
        for key, pool in (("imp_any", imps), ("imp_or_str", None), ("str_any", None)):
            if ok and key in spec:
                got = [i for i in spec[key] if (i in pool if pool is not None else
                                                i in imps or i in text)]
                ok = bool(got)
                ev += got
        if not ok:
            continue
        if not ev:
            continue
        hit.add(tid)
        tech(tid if tid not in ("note", "macro", "pdf") else "", what, tactic, w,
             ", ".join(dict.fromkeys(ev))[:120])

    info = []
    for label, imp_set, str_list in INFO:
        got = sorted(imp_set & imps) + [s for s in str_list if s in text]
        if got:
            info.append((label, ", ".join(got[:3])))
    crypto = any(l == "encrypts data" for l, _ in info)
    walks = any(l.startswith("lists files") for l, _ in info)

    # T1486 = the ransomware core: encrypt + walk folders + (note | backup wipe)
    if crypto and walks and ("note" in hit or "T1490" in hit):
        tech("T1486", "Data Encrypted for Impact -- RANSOMWARE: encrypts the files it walks "
             "through and %s" % ("leaves a ransom note" if "note" in hit else "wipes backups"),
             "Impact", 6, next(e for l, e in info if l == "encrypts data"))
    if pe:
        packed = [n or "?" for n, e in pe["sections"] if e > 7.4 or n.upper().startswith("UPX")]
        if packed:
            tech("T1027.002", "Software Packing (hides its code; some normal installers do "
                 "this too)", "Defense Evasion", 1, ", ".join(packed[:3]))
        if pe.get("broken"):
            findings.append(("malformed program header", ""))
        if pe["signed"]:
            findings.append(("carries a digital signature (not verified here)", ""))
        if not pe["dotnet"] and pe["imports"] and len(imps) < 8:
            findings.append(("imports almost nothing -- it may load its real code at runtime", ""))
    elif cat in ("pe", "elf") and _entropy(data) > 7.5:
        tech("T1027.002", "Software Packing (contents encrypted/compressed)", "Defense Evasion", 1, "")

    level = _level(score)
    kind = ("Windows program" + (" (.NET)" if pe and pe["dotnet"] else "") +
            (" 64-bit" if pe and pe.get("x64") else "") if pe else
            "Linux program" if cat == "elf" else
            "script" if is_script else ftype.split(",")[0][:50] or "file")
    return dict(base, type=kind, score=score, level=level, techniques=techs, info=info,
                findings=findings, exe=cat in ("pe", "elf"), script=is_script,
                imports={k: v[:40] for k, v in ((pe or {}).get("imports") or {}).items()},
                strings=[s for s in strs if 6 <= len(s) <= 120 and re.search(r"[a-z]{3}", s)])


def _level(s):
    return ("likely malicious" if s >= 8 else "suspicious" if s >= 5 else
            "worth a look" if s >= 2 else "nothing dangerous found")


def vt_lookup(sha256, key):
    """VirusTotal verdict for a HASH (the file is never uploaded). None = no answer."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://www.virustotal.com/api/v3/files/" + sha256,
                                 headers={"x-apikey": key})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            a = json.loads(r.read().decode())["data"]["attributes"]
    except urllib.error.HTTPError as e:
        return {"known": False} if e.code == 404 else None
    except (OSError, ValueError, KeyError):
        return None
    stats = a.get("last_analysis_stats") or {}
    names = [v.get("result") for v in (a.get("last_analysis_results") or {}).values()
             if v.get("category") == "malicious" and v.get("result")]
    return {"known": True, "malicious": stats.get("malicious", 0),
            "suspicious": stats.get("suspicious", 0),
            "total": sum(stats.get(k, 0) for k in ("malicious", "suspicious", "undetected", "harmless")),
            "label": ((a.get("popular_threat_classification") or {}).get("suggested_threat_label")
                      or (names[0] if names else "")),
            "name": (a.get("meaningful_name") or "")}


def _clam(paths):
    """ClamAV signature hits {path: virus name}, or None when it isn't usable."""
    exe = shutil.which("clamdscan") or shutil.which("clamscan")
    if not exe or not paths:
        return None
    try:
        r = subprocess.run([exe, "--no-summary", "--infected"] + paths,
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 2:
        return None                       # installed but no signature database yet
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"(.+?): (.+) FOUND$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _walk(root):
    if os.path.isfile(root):
        return [root]
    files = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("System Volume Information", "$RECYCLE.BIN",
                                                ".Trash-1000", "node_modules", ".git")]
        for f in fs:
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                files.append(p)
            if len(files) >= MAX_FILES:
                return files
    return files


def _archive_members(path, root):
    """Unpack an archive into a temp dir (size-capped) so its files get checked."""
    if not shutil.which("bsdtar"):
        return None, []
    try:
        listing = subprocess.run(["bsdtar", "-tvf", path], capture_output=True, text=True,
                                 timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None, []
    total = sum(int(p[4]) for p in (l.split() for l in listing.splitlines())
                if len(p) > 4 and p[4].isdigit())
    if not listing or total > 300 * 1024 * 1024:
        return None, []
    tmp = tempfile.mkdtemp(prefix="tar-scan-")
    try:
        subprocess.run(["bsdtar", "-xf", path, "-C", tmp], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    rel = os.path.relpath(path, root) if os.path.isdir(root) else os.path.basename(path)
    return tmp, [(p, rel + " → " + os.path.relpath(p, tmp)) for p in _walk(tmp)]


def scan(root, vt_key=None):
    root = os.path.abspath(os.path.expanduser(root))
    files = _walk(root)
    results, temps = [], []
    for p in files:
        shown = os.path.relpath(p, root) if os.path.isdir(root) else os.path.basename(p)
        try:
            results.append(analyze(p, shown))
        except OSError as e:
            results.append({"name": shown, "path": p, "level": "unreadable", "score": 0,
                            "techniques": [], "info": [], "findings": [(str(e), "")],
                            "type": "?", "sha256": ""})
            continue
        if os.path.splitext(p)[1].lower() in ARCHIVE_EXT:
            tmp, members = _archive_members(p, root)
            if tmp:
                temps.append(tmp)
                for mp, mshown in members[:200]:
                    try:
                        results.append(analyze(mp, mshown))
                    except OSError:
                        pass
    if os.path.isdir(root):
        # USB-worm tricks live at the drive root, not inside one file
        for r in results:
            n = r["name"].lower()
            if n == "autorun.inf":
                r["score"] += 3
                r["techniques"].append({"id": "T1091", "what": "autorun.inf at the drive root: "
                                        "tries to run something when plugged in",
                                        "tactic": "Lateral Movement", "evidence": n})
            elif "/" not in n and n.endswith(".lnk"):
                r["score"] += 2
                r["techniques"].append({"id": "T1547.009", "what": "shortcut at the drive root -- "
                                        "classic USB-worm trick to launch a hidden program",
                                        "tactic": "Persistence", "evidence": n})
    clam = _clam([r["path"] for r in results if r.get("sha256")])
    for r in results:
        if clam and r["path"] in clam:
            r["score"] += 10
            r["findings"].insert(0, ("ClamAV signature match", clam[r["path"]]))
        if r.get("level") not in ("test file", "unreadable"):
            r["level"] = _level(r["score"])
    if vt_key:
        # only programs/scripts/flagged files: the free API allows 4 lookups a minute
        todo = [r for r in results if r.get("sha256") and (r.get("exe") or r.get("script")
                                                          or r["score"] >= 2)][:4]
        for r in todo:
            r["vt"] = vt_lookup(r["sha256"], vt_key)
            v = r["vt"] or {}
            if v.get("known") and v.get("malicious", 0) >= 3:
                r["score"] += 10
                r["level"] = _level(r["score"])
    for t in temps:
        shutil.rmtree(t, ignore_errors=True)
    results.sort(key=lambda r: -r.get("score", 0))
    return {"root": root, "files": results, "clamav": clam is not None, "vt": bool(vt_key),
            "truncated": len(files) >= MAX_FILES}


if __name__ == "__main__":
    out = scan(sys.argv[1] if len(sys.argv) > 1 else ".")
    for r in out["files"]:
        r.pop("strings", None)
        r.pop("imports", None)
    print(json.dumps(out, indent=1, ensure_ascii=False))
