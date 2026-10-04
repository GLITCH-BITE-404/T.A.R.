import QtQuick
import QtQuick.Window
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import "../"

// T.A.R. -- local offline assistant for BITE-OS.
// UI only; inference/memory/tiering all live in tar_brain.py.
Item {
    id: window
    focus: true
    // shake / barrel roll from the effects layer move the whole panel
    transform: [
        Translate { x: fx.shakeX; y: fx.shakeY },
        Rotation { origin.x: window.width / 2; origin.y: window.height / 2
                   angle: fx.spin }
    ]

    MatugenColors { id: theme }

    Scaler {
        id: scaler
        currentWidth: Screen.width
        currentHeight: Screen.height
    }
    function s(val) { return scaler.s(val); }

    readonly property string brainPath:
        Quickshell.env("HOME") + "/.config/hypr/scripts/quickshell/tar/tar_brain.py"

    // --- backend state ---
    property string mode: "boot"       // boot|idle|listening|thinking|speaking|error
    property string activeModel: ""
    property string activeTier: ""
    property string activeLane: ""
    property string statusNote: "booting"
    property bool busy: false
    property real orbLevel: 0.0
    property int streamIndex: -1
    property string rawStream: ""   // untrimmed tokens, before directives are hidden

    // --- presentation ---
    property string viewMode: "orb"     // "orb" (jarvis) | "chat" (llm window)
    property string lastUser: ""
    property string lastReply: ""
    property bool micEnabled: false
    property bool speakReplies: false
    property real ttsVolume: 0.7

    // --- fullscreen intro ---
    // The window is screen-sized; the console collapses into the middle of it.
    // When hosted in a real window the panel IS the window -- filling it, with
    // a small inset for the chrome. The fractional sizing below only makes
    // sense on a screen-sized surface, where the panel is a card floating in
    // the middle of the display.
    property bool windowed: false
    property string startMode: "cinematic"   // window | cinematic | floating

    // Cloud mode is visually distinct on purpose: it costs money and leaves the
    // machine, so it should never be mistaken for the offline local brain.
    property string backend: "local"
    property string cloudModel: "claude-opus-5"
    readonly property bool cloudMode: backend === "claude"
    property var autostart: ({})
    property bool autoApplied: false

    // Applied once, after the first caps report tells us what the user wants
    // switched on at launch.
    function applyAutostart() {
        var a = window.autostart || ({});
        window.sfxEnabled = a.sfx !== false;
        if (a.speak) {
            window.speakReplies = true;
            window.capsRun(["speak", "on"]);
        }
        if (a.mic) {
            var stt = window.capsData ? window.capsData.stt : undefined;
            if (stt && stt.ready) { window.micEnabled = true; window.listen(); }
            else window.say("sys", "autostart wanted the mic, but no speech "
                                 + "engine is installed.");
        }
        if (a.greet) {
            var tts = window.capsData ? window.capsData.tts : undefined;
            if (tts && tts.ready) window.speak("T.A.R. online.");
        }
    }
    readonly property real targetW: windowed
        ? window.width - s(16)
        : Math.min(window.width * 0.62, s(900))
    readonly property real targetH: windowed
        ? window.height - s(16)
        : Math.min(window.height * 0.88, s(760))
    property real frameW: window.width
    property real frameH: window.height
    property real frameAlpha: 0.0        // shell chrome fades in as it lands

    // Panel + console are laid out as ONE group that stays centred. Opening
    // the console makes T.A.R. step aside and narrow itself to fit, instead
    // of letting the console hang off the edge.
    readonly property real consoleW: s(330)
    readonly property real consoleGap: s(12)
    readonly property real panelW: consoleOpen
        ? Math.max(s(360), Math.min(targetW,
                   window.width - consoleW - consoleGap - s(56)))
        : targetW
    property real introBurst: 0.0        // 0..1 expanding shockwave
    property bool introDone: false

    // --- capabilities (tar_caps.py) ---
    // What T.A.R. can actually DO on this box. Anything with no engine gets
    // walled off in the settings pane rather than failing silently at use time.
    readonly property string capsPath:
        Quickshell.env("HOME") + "/.config/hypr/scripts/quickshell/tar/tar_caps.py"
    property bool settingsOpen: false
    property bool historyOpen: false
    property string currentSession: ""
    property var capsData: ({})
    property var devicesData: ({})
    property string capsVoice: ""
    property bool capsSpeak: false
    property string capBusy: ""          // cap currently installing
    property int capPct: -1
    property string capLabel: ""

    // --- console ---
    property bool consoleOpen: false
    // Opening a side panel should make the window BIGGER, not squash the chat.
    onConsoleOpenChanged: window.autoSize()
    onSettingsOpenChanged: window.autoSize()
    onHistoryOpenChanged: window.autoSize()
    onTestModeChanged: window.autoSize()
    function autoSize() {
        if (!window.windowed) return;
        var wide = window.consoleOpen || window.settingsOpen
                   || window.historyOpen || window.testMode !== "";
        window.requestSize(wide ? 1420 : 1040, wide ? 860 : 760);
    }
    property bool autotier: true
    ListModel { id: modelsModel }
    ListModel { id: memoryModel }

    // Local mode rides the mauve/blue rice palette. Cloud mode shifts to a
    // warmer peach/yellow so you can tell at a glance that requests are
    // leaving the machine and costing money.
    // NOT readonly: a Behavior needs to be able to write the property, and
    // attaching one to a readonly property throws "Invalid property
    // assignment", which breaks the binding and leaves the whole window
    // painting nothing -- an invisible window.
    property color accent: mode === "error" ? theme.red
                         : mode === "thinking"
                             ? (cloudMode ? theme.yellow : theme.blue)
                         : mode === "listening" ? theme.teal
                         : (cloudMode ? theme.peach : theme.mauve)
    Behavior on accent { ColorAnimation { duration: 450 } }

    readonly property string brandLine: cloudMode
        ? "T.A.R. // CLOUD \u2014 " + String(cloudModel).replace("claude-", "")
        : "T.A.R. AIN'T RESTRICTED"

    // The host decides what closing means: the harness quits the process, the
    // rice tells qs_manager to hide the panel. Previously this ALWAYS called
    // qs_manager, which does nothing when T.A.R. is run standalone -- so the X
    // button and Esc both silently did nothing.
    signal closed()
    property bool hostHandlesClose: false

    // Closing the window must take the whole assistant down with it. It didn't:
    // keep_alive kept llama-server resident at ~65% CPU and 31% RAM long after
    // the UI was gone, with no obvious process to find in btop.
    Process {
        id: reaper
        command: ["python3", window.brainPath, "shutdown"]
    }
    function shutdown() {
        if (brain.running)      brain.running = false;
        if (panelProc.running)  panelProc.running = false;
        if (capsProc.running)   capsProc.running = false;
        if (meterProc.running)  meterProc.running = false;
        if (listenProc.running) listenProc.running = false;
        if (camProc.running)    camProc.running = false;
        camTick.stop();
        reaper.running = true;      // unloads the model from ollama
    }

    function close() {
        window.shutdown();
        closeDefer.start();
    }
    Timer {
        id: closeDefer
        interval: 350
        onTriggered: window.finishClose()
    }
    function finishClose() {
        window.closed();
        if (!window.hostHandlesClose) {
            Quickshell.execDetached(["bash",
                Quickshell.env("HOME") + "/.config/hypr/scripts/qs_manager.sh",
                "close"]);
        }
    }

    // Esc peels one layer at a time instead of nuking the window from any
    // state -- dismiss the chooser, then a pane, and only then close.
    readonly property bool anyPaneOpen:
        choiceOpen || historyOpen || settingsOpen || testMode !== ""

    function escapeLayer() {
        if (window.choiceOpen)     { window.choiceOpen = false;   return; }
        if (window.testMode !== "") { window.closeTest();         return; }
        if (window.historyOpen)    { window.historyOpen = false;  return; }
        if (window.settingsOpen)   { window.settingsOpen = false; return; }
        // Busy with nothing open: stop the reply rather than kill the window.
        if (window.busy)           { window.stopGeneration();     return; }
        window.close();
    }
    Keys.onEscapePressed: (event) => { window.escapeLayer(); event.accepted = true; }

    // Keys.onEscapePressed only fires if THIS item has focus. Once you click
    // into the setup pane or the transcript, focus moves and Esc did nothing --
    // which is why it felt stuck while the model was thinking. A window-scoped
    // Shortcut fires no matter what holds focus.
    Shortcut {
        sequences: ["Escape"]
        context: Qt.WindowShortcut
        onActivated: window.escapeLayer()
    }
    Shortcut {
        sequences: ["Ctrl+C"]
        context: Qt.WindowShortcut
        onActivated: if (window.busy) window.stopGeneration()
    }

    // positionViewAtEnd() runs before the new delegate has been measured, so
    // long replies left the view a line short. Re-pin on the next tick.
    Timer {
        id: bootSfx
        interval: 400
        onTriggered: window.sfx("boot")
    }

    // positionViewAtEnd() alone was unreliable: it runs before the new delegate
    // has been measured, and does nothing while content is still growing. Drive
    // contentY directly, and repeat a couple of times as the text wraps.
    // Autoscroll, attempt three. The previous versions failed because
    // contentHeight is still growing when they ran: positionViewAtEnd() uses
    // the OLD height, and a one-shot contentY assignment gets overwritten by
    // the ListView's own relayout a frame later. Binding-style pinning is the
    // reliable form -- stay glued to the bottom unless the user scrolls up.
    property bool pinBottom: true
    // exposed so scroll behaviour can be asserted, not eyeballed
    // temporary diagnostic
    function settingsGeom() {
        return "w=" + Math.round(settingsPane.width)
             + " h=" + Math.round(settingsPane.height)
             + " vis=" + settingsPane.visible
             + " launchH=" + Math.round(settingsPane.launchBlockHeight);
    }
    // Never compute contentY by hand: a ListView's scroll range moves (originY)
    // as delegates of different heights get laid out, so every hand-rolled
    // "bottom" either fell short or overshot into empty space mid-stream.
    // positionViewAtEnd() uses the real item positions; Qt.callLater runs it
    // after the layout settles, and returnToBounds() snaps back if anything
    // ever lands past the end.
    function scrollState() {
        if (!transcript) return "no transcript";
        return "contentY=" + Math.round(transcript.contentY)
             + " atEnd=" + transcript.atYEnd;
    }
    function pinNow() {
        if (!transcript || !window.pinBottom) return;
        transcript.positionViewAtEnd();
        transcript.returnToBounds();
    }
    function scrollToEnd() {
        if (!transcript) return;
        window.pinBottom = true;
        Qt.callLater(window.pinNow);
    }
    Timer {
        id: scrollPin
        interval: 50
        repeat: true
        property int ticks: 0
        onTriggered: {
            window.scrollToEnd();
            ticks++;
            if (ticks > 6) { stop(); ticks = 0; }
        }
        onRunningChanged: if (running) ticks = 0
    }

    Timer {
        id: focusTimer
        interval: 50
        onTriggered: {
            if (window.viewMode === "orb") orbView.focusInput();
            else input.forceActiveFocus();
        }
    }
    onViewModeChanged: focusTimer.restart()
    Component.onCompleted: {
        focusTimer.restart();
        probe.running = true;
        introSeq.start();
        window.capsRefresh();     // know what's locked before the user asks
        bootSfx.start();
        // Prime the model + prompt cache now, while the intro animation plays.
        // Cold, the first reply took ~34s; warmed, the same prefix costs ~0.4s,
        // so this is the single biggest thing standing between T.A.R. and
        // feeling like it works.
        warmDefer.start();   // after caps tells us whether warming is wanted
    }

    Timer {
        id: warmDefer
        interval: 900
        onTriggered: {
            var a = window.autostart || ({});
            if (a.warm !== false) warmProc.running = true;
        }
    }
    Process {
        id: warmProc
        command: ["python3", window.brainPath, "warm"]
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleLine(data) }
    }

    SequentialAnimation {
        id: introSeq
        // phase 1 -- a fullscreen energy field blows outward
        NumberAnimation { target: window; property: "introBurst"
                          from: 0.0; to: 1.0; duration: 900
                          easing.type: Easing.OutCubic }
        // phase 2 -- everything implodes into the console
        ParallelAnimation {
            NumberAnimation { target: window; property: "frameW"
                              to: window.targetW; duration: 1150
                              easing.type: Easing.OutExpo }
            NumberAnimation { target: window; property: "frameH"
                              to: window.targetH; duration: 1150
                              easing.type: Easing.OutExpo }
            NumberAnimation { target: window; property: "frameAlpha"
                              to: 1.0; duration: 900; easing.type: Easing.OutCubic }
            NumberAnimation { target: window; property: "introBurst"
                              to: 0.0; duration: 700; easing.type: Easing.InCubic }
        }
        ScriptAction { script: window.introDone = true }
    }

    Connections {
        target: window
        function onVisibleChanged() { if (window.visible) focusTimer.restart(); }
    }

    Timer {
        id: levelDecay
        interval: 80; repeat: true; running: window.orbLevel > 0.01
        onTriggered: window.orbLevel = Math.max(0, window.orbLevel - 0.12)
    }

    ListModel { id: chatModel }
    ListModel { id: sessionsModel }
    ListModel { id: choiceModel }

    // ---- slash command palette -------------------------------------------
    // Typing "/" opens a filtered list, the way Claude Code does it: you see
    // what exists instead of having to remember it, and Tab/Enter completes.
    ListModel { id: cmdModel }
    property var allCommands: [
        { cmd: "settings", args: "",            desc: "open the setup pane" },
        { cmd: "chats",    args: "",            desc: "browse past conversations" },
        { cmd: "clear",    args: "",            desc: "clear this transcript" },
        { cmd: "stop",     args: "",            desc: "stop the reply in progress" },
        { cmd: "cloud",    args: "[off]",       desc: "switch to the cloud brain (Gemini/Claude)" },
        { cmd: "new",      args: "",            desc: "start a new chat" },
        { cmd: "persona",  args: "<how to talk> | reset", desc: "change how T.A.R. talks" },
        { cmd: "key",      args: "<api-key>",   desc: "save your Anthropic API key" },
        { cmd: "model",    args: "<name>",      desc: "pin a specific local model" },
        { cmd: "auto",     args: "",            desc: "back to automatic model choice" },
        { cmd: "lane",     args: "safe|open",   desc: "stock or abliterated local models" },
        { cmd: "speak",    args: "[on|off]",    desc: "read replies out loud" },
        { cmd: "forget",   args: "<text>",      desc: "drop a remembered fact" },
        { cmd: "undo",     args: "",            desc: "revert the last settings change" },
        { cmd: "history",  args: "",            desc: "browse past conversations" }
    ]
    property int cmdIndex: 0
    readonly property bool cmdOpen: cmdModel.count > 0

    function refreshCommands(text) {
        cmdModel.clear();
        window.cmdIndex = 0;
        if (!text || text.charAt(0) !== "/") return;
        // only while still typing the command word itself
        if (text.indexOf(" ") >= 0) return;
        var q = text.substring(1).toLowerCase();
        for (var i = 0; i < window.allCommands.length; i++) {
            var c = window.allCommands[i];
            if (q === "" || c.cmd.indexOf(q) === 0)
                cmdModel.append(c);
        }
    }
    function applyCommand(i) {
        if (i < 0 || i >= cmdModel.count) return;
        var c = cmdModel.get(i);
        input.text = "/" + c.cmd + (c.args !== "" ? " " : "");
        input.cursorPosition = input.text.length;
        cmdModel.clear();
        // no args needed -> just run it
        if (c.args === "") window.submit(input.text);
    }
    property bool choiceOpen: false
    property string choiceCap: ""
    property string choiceTitle: ""

    function openHistory() {
        window.viewMode = "chat";
        window.settingsOpen = false;
        window.historyOpen = true;
        window.panelRun(["sessions"]);
    }
    function loadSession(sid) {
        window.panelRun(["session-open", sid]);
        window.historyOpen = false;
    }
    function newSession() {
        chatModel.clear();
        window.lastUser = ""; window.lastReply = "";
        window.panelRun(["session-new"]);
        window.historyOpen = false;
    }

    // `tar-ai new` from a terminal:  qs ipc -p <TarHarness.qml> call tar newChat
    IpcHandler {
        target: "tar"
        function newChat(): void { window.newSession(); }
    }

    // Orb/panel effects an action can trigger. These are UI-only: tar_tools
    // emits them instead of running anything, so the renderer stays the only
    // thing that knows how the orb actually moves.
    // The host window, if there is one, so T.A.R. can resize itself and grow
    // when a side panel opens instead of squeezing the chat into what's left.
    signal sizeRequested(int w, int h)
    function requestSize(w, h) { window.sizeRequested(w, h); }

    property real rainBoost: 1.0
    property bool alertFlash: false
    function playUiAction(name, d) {
        if (name === "close")           { window.close(); return; }
        if (name === "newchat")         { window.newSession(); return; }
        if (name === "collapse" || name === "compact") {
            window.viewMode = "orb";
            window.settingsOpen = false; window.historyOpen = false;
            window.requestSize(760, 640);
            return;
        }
        if (name === "expand" || name === "fullpanel") {
            window.viewMode = "chat";
            window.requestSize(1180, 820);
            return;
        }
        if (name === "selfsize") {
            window.requestSize(parseInt(d.w) || 1040, parseInt(d.h) || 760);
            return;
        }
        if (name === "calm")            { window.mode = "idle"; orbView.settle();
                                          window.rainBoost = 1.0; return; }
        if (name === "rain")            { window.rainBoost =
                                              window.rainBoost > 1.5 ? 1.0 : 2.4;
                                          return; }
        // Everything visual goes through the effects layer, which sits over
        // both views -- these used to animate the chat-view orb only, so in
        // orb view (the default) nothing visible happened.
        if (name === "pulse")           { orb.pulse(); fx.play("shock"); return; }
        if (name === "anim") {
            var which = String(d.name || d.what || "shock");
            fx.play(fx.effects.indexOf(which) >= 0 ? which : "shock", d.text);
            return;
        }
        if (name === "alert")           { window.mode = "error"; orb.pulse();
                                          fx.play("alert"); alertHold.restart(); return; }
        if (fx.effects.indexOf(name) >= 0) { orb.pulse(); fx.play(name, d.text); return; }
    }
    Timer { id: alertHold; interval: 1600; onTriggered: {
        window.mode = "idle"; orbView.settle(); } }
    SequentialAnimation {
        id: scanSweep
        NumberAnimation { target: window; property: "scanY"; from: 0.0; to: 1.0
                          duration: 850; easing.type: Easing.InOutSine }
    }
    property real scanY: -1

    function say(who, text) {
        // Orb mode has no transcript -- surface it as a popup there instead of
        // letting it vanish.
        if (window.viewMode === "orb"
            && (who === "sys" || who === "act")) {
            orbView.showToast(text, who === "act" ? "act"
                            : (window.mode === "error" ? "error" : "sys"));
        }
        chatModel.append({ who: who, text: text });
        window.scrollToEnd();
        scrollPin.restart();          // keep pinned while the text wraps
        if (who === "you") { window.lastUser = text; window.lastReply = ""; }
        else if (who === "tar") window.lastReply = text;
        return chatModel.count - 1;
    }

    // ------------------------------------------------------------------ backend

    function handleLine(line) {
        line = (line || "").trim();
        if (line === "" || line.charAt(0) !== "{") return;
        var d;
        try { d = JSON.parse(line); } catch (e) { return; }

        if (d.t === "state") {
            if (d.v === "thinking") {
                window.mode = "thinking"; window.statusNote = "thinking";
                if (d.model) { window.activeModel = d.model;
                               window.activeTier = d.tier || "";
                               window.activeLane = d.lane || ""; }
            } else if (d.v === "speaking") {
                window.mode = "speaking"; window.statusNote = "transmitting";
            } else if (d.v === "pulling") {
                window.mode = "thinking"; window.statusNote = "fetching model";
            } else if (d.v === "idle") {
                window.mode = "idle"; window.statusNote = "ready";
            }
        } else if (d.t === "token") {
            // The directive is stripped server-side only when the reply is
            // complete, so during streaming it was appearing on screen as
            // "<<ACT cam_snap>>". Hide it as it arrives.
            if (window.streamIndex < 0) {
                window.streamIndex = say("tar", "");
                window.rawStream = "";
                orbView.surfaceReply();     // orb spits the answer back up
            }
            window.rawStream += (d.v || "");
            // Hide a directive as soon as it starts: once we see "<<" we stop
            // revealing text until the matching ">>" lands. Models also emit a
            // bare "ACT open what=x" with no brackets, so drop that too.
            // Hide directives as they stream. The model produces malformed
            // variants constantly ("ACT snap picture", "ACT open what=x y.jpg"),
            // so match the whole line from "ACT" onward rather than trying to
            // describe valid argument syntax.
            var shown = window.rawStream
                .replace(/<<ACT\b[\s\S]*?>>/g, "")
                .replace(/<<\s*ACT\b[\s\S]*$/, "")
                .replace(/^[ \t]*ACT\b.*$/gm, "")
                .replace(/^[ \t]*ACT\b.*$/, "")
                .replace(/\n{3,}/g, "\n\n");
            var grown = shown.replace(/^\s+/, "");
            chatModel.setProperty(window.streamIndex, "text", grown);
            window.lastReply = grown;
            window.orbLevel = Math.min(1.0, window.orbLevel + 0.35);
            window.scrollToEnd();
        } else if (d.t === "pull") {
            window.statusNote = "fetching  " + (d.pct !== undefined ? d.pct + "%" : "");
        } else if (d.t === "mem") {
            say("sys", "committed to memory: " + d.v);
        } else if (d.t === "info") {
            // Engine bookkeeping ("speed: reusing loaded ...", "unloaded X to
            // free RAM") is diagnostics, not conversation. It belongs in the
            // status line, not interleaved with what you and T.A.R. said.
            var quiet = /^(speed:|unloaded |warming )/.test(d.v || "");
            if (quiet) window.statusNote = d.v;
            else say("sys", d.v);
        } else if (d.t === "suggest_upgrade") {
            // The model visibly failed. Offer a bigger one that FITS -- never
            // switch silently, and never propose something that would swap.
            window.say("sys", d.v || "try a bigger model?");
            window.sfx("warn");
            choiceModel.clear();
            choiceModel.append({
                cap: "__model__", engine: d.target, ready: true,
                title: "Retry with " + String(d.target).split("/").pop(),
                sub: d.size_gb + " GB  ·  fits your " + d.budget_gb + " GB free",
                desc: "switches T.A.R. to this model and re-sends your message"
            });
            choiceModel.append({
                cap: "__model__", engine: "__keep__", ready: false,
                title: "Keep " + String(d.current).split("/").pop(),
                sub: "faster, but it just " + (d.reason || "failed"),
                desc: "stay on the current model"
            });
            window.pendingRetry = d.retry || "";
            window.choiceTitle = "model";
            window.choiceOpen = true;
        } else if (d.t === "warm") {
            if (d.ok === true) window.statusNote = "warm   " + (d.v || "");
            else if (d.ok === false) window.say("sys", d.v || "warm failed");
        } else if (d.t === "backend") {
            window.backend = d.which || "local";
            window.say("act", d.v || ("brain: " + window.backend));
            window.sfx(window.backend === "claude" ? "boot" : "close");
            window.capsRefresh();
            window.consoleRefresh();      // active marker moves between tabs
        } else if (d.t === "cloud_status") {
            window.say("sys", "cloud: " + (d.v || ""));
        } else if (d.t === "settings_changed") {
            // T.A.R. just changed its own configuration -- re-read it so the
            // setup pane shows the new state instead of the stale one.
            window.capsRefresh();
            window.flashSetup();
        } else if (d.t === "acted") {
            // An action ran -- either matched outright (no model) or requested
            // by the model via <<ACT ...>>. Show it as a distinct line so it
            // never reads like something T.A.R. merely claimed to do.
            window.say("act", (d.v || d.action));
            orb.pulse();
            window.sfx("act");
        } else if (d.t === "ui") {
            window.playUiAction(d.action, d);
        } else if (d.t === "windows") {
            var wl = (d.rows || []).map(function (w) {
                return "  " + (w.ws ? "[" + w.ws + "] " : "")
                     + (w.title || w.class);
            }).join("\n");
            window.say("act", "windows:\n" + wl);
        } else if (d.t === "grep") {
            window.say("act", (d.hits || []).length
                ? (d.hits || []).join("\n") : "no hits for " + d.q);
        } else if (d.t === "file") {
            window.say("act", d.body || "");
        } else if (d.t === "image") {
            window.say("act", "captured " + (d.path || ""));
            if (d.path) window.showImage(d.path);
        } else if (d.t === "sysinfo") {
            window.say("act", d.v || "");
        } else if (d.t === "nomatch") {
            // nothing: the brain falls through to inference on its own
        } else if (d.t === "done") {
            if (window.streamIndex >= 0) {
                // the brain has stripped the action directives by now, so the
                // final text is authoritative over anything we streamed
                var finalTxt = (d.reply || "").replace(/^\s+/, "");
                if (finalTxt.length > 0) {
                    chatModel.setProperty(window.streamIndex, "text", finalTxt);
                    window.lastReply = finalTxt;
                } else {
                    // reply was ONLY a directive -- drop the empty bubble
                    chatModel.remove(window.streamIndex);
                    window.lastReply = "";
                }
            } else if (d.reply) {
                say("tar", d.reply);
            }
            if (d.model) window.activeModel = d.model;
            window.statusNote = "ready   " + (Math.round(d.ms / 100) / 10) + "s"
                + (d.first_token_ms ? "   first " + d.first_token_ms + "ms" : "");
            window.streamIndex = -1;
            window.busy = false;
            window.mode = "idle";
            orbView.settle();
            if (window.speakReplies && d.reply) window.speak(d.reply);
        } else if (d.t === "probe") {
            window.activeModel = d.model || "";
            window.activeTier = d.tier || "";
            window.activeLane = d.lane || "";
            window.statusNote = d.reason || "ready";
            window.mode = "idle";
            orb.boot();
            if (d.tier !== "cloud" && !d.ollama)
                say("sys", "ollama offline — starting it on your first message.");
            if (d.tier !== "cloud" && d.installed && d.installed.indexOf(d.model) === -1)
                say("sys", d.model + " not on disk; first message will fetch it.");
        } else if (d.t === "models") {
            var out = "MODELS   · installed   > active\n\n";
            for (var i = 0; i < d.rows.length; i++) {
                var r = d.rows[i];
                out += (r.active ? " > " : r.installed ? " · " : "   ")
                     + r.model + "   [" + r.tier + "/" + r.lane + "]  "
                     + (r.size_gb ? r.size_gb + "GB" : "") + "\n";
            }
            out += "\n/model <name>   ·   /model gemini-flash-lite-latest (cloud)   ·   /cloud off   ·   /auto";
            say("sys", out.trim());
            window.busy = false; window.mode = "idle";
        } else if (d.t === "mem_list") {
            if (!d.facts || d.facts.length === 0) {
                say("sys", "memory empty. teach me: remember that ...");
            } else {
                var m = "MEMORY (" + d.facts.length + ")\n\n";
                for (var j = 0; j < d.facts.length; j++)
                    m += "  · " + d.facts[j].text + "\n";
                say("sys", m.trim());
            }
            window.busy = false; window.mode = "idle";
        } else if (d.t === "ok") {
            say("sys", d.v + (d.model ? "   →   " + d.model : ""));
            if (d.model) window.activeModel = d.model;
            window.busy = false; window.mode = "idle";
            probe.running = true;
        } else if (d.t === "error") {
            say("sys", "ERR  " + d.v);
            window.statusNote = "error";
            window.mode = "error";
            window.streamIndex = -1;
            window.busy = false;
        }
    }

    Process {
        id: probe
        command: ["python3", window.brainPath, "probe"]
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleLine(data) }
    }

    Process {
        id: brain
        property var argv: []
        command: argv
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleLine(data) }
        stderr: SplitParser { splitMarker: "\n"; onRead: data => console.log("T.A.R.:", data) }
        onExited: (code, status) => {
            if (window.busy) {
                window.say("sys", "backend died (code " + code + ")");
                window.busy = false; window.mode = "error"; window.streamIndex = -1;
            }
        }
    }

    // Console traffic runs on its own process; chat inference can be mid-stream.
    property var panelQueue: []
    // Same guard as capsBusy below: Process.running is not set synchronously,
    // so two panelRun() calls in one tick would clobber the first one's argv.
    property bool panelBusy: false
    Process {
        id: panelProc
        property var argv: []
        command: argv
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handlePanelLine(data) }
        onExited: (code, status) => { window.panelBusy = false; window.panelNext(); }
    }
    function panelRun(args) {
        window.panelQueue.push(args);
        window.panelNext();
    }
    function panelNext() {
        if (window.panelBusy) return;
        if (window.panelQueue.length === 0) return;
        var next = window.panelQueue.shift();
        window.panelBusy = true;
        panelProc.argv = ["python3", window.brainPath].concat(next);
        panelProc.running = true;
    }

    // --- capability traffic --------------------------------------------------
    // Separate again from both of the above: an install streams progress for
    // minutes and must not block a chat reply or a console refresh.
    property var capsQueue: []
    // Guard with our own flag, NOT capsProc.running: assigning running = true
    // does not take effect synchronously, so a second capsRun() in the same
    // tick would still see false and overwrite the argv of the call already
    // in flight. That silently dropped `caps` whenever `devices` followed it.
    property bool capsBusy: false
    Process {
        id: capsProc
        property var argv: []
        command: argv
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleCapsLine(data) }
        stderr: SplitParser { splitMarker: "\n"; onRead: data => console.log("T.A.R./caps:", data) }
        onExited: (code, status) => { window.capsBusy = false; window.capsNext(); }
    }
    function capsRun(args) {
        window.capsQueue.push(args);
        window.capsNext();
    }
    function capsNext() {
        if (window.capsBusy) return;
        if (window.capsQueue.length === 0) return;
        var next = window.capsQueue.shift();
        window.capsBusy = true;
        capsProc.argv = ["python3", window.capsPath].concat(next);
        capsProc.running = true;
    }
    // You asked for this twice: when something in setup actually changes, the
    // pane closes and comes back a beat later. Silent in-place updates are
    // indistinguishable from "nothing happened", which is why RESCAN felt dead.
    property bool setupWasOpen: false
    Timer {
        id: setupBlink
        interval: 620
        onTriggered: { window.settingsOpen = window.setupWasOpen; }
    }
    function flashSetup() {
        if (!window.settingsOpen) return;
        window.setupWasOpen = true;
        window.settingsOpen = false;
        setupBlink.restart();
    }

    function capsRefresh() {
        window.capsRun(["caps"]);
        window.capsRun(["devices"]);
    }

    // Installs get their OWN process. They now block for as long as the
    // terminal takes (minutes), and sharing the caps queue meant RESCAN and
    // every other capability query silently piled up behind them -- which is
    // exactly why RESCAN looked dead.
    Process {
        id: installProc
        property var argv: []
        command: argv
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleCapsLine(data) }
        stderr: SplitParser { splitMarker: "\n"; onRead: data => console.log("T.A.R./install:", data) }
        onExited: (code, status) => {
            window.capBusy = ""; window.capPct = -1;
            window.capsRefresh();
        }
    }
    function installRun(args) {
        if (installProc.running) {
            window.say("sys", "an install is already running — watch the terminal.");
            return;
        }
        installProc.argv = ["python3", window.capsPath].concat(args);
        installProc.running = true;
    }

    function handleCapsLine(line) {
        line = (line || "").trim();
        if (line === "" || line.charAt(0) !== "{") return;
        var d;
        try { d = JSON.parse(line); } catch (e) { return; }

        if (d.t === "caps") {
            window.capsData = d.caps || ({});
            window.startMode = d.start_mode || "cinematic";
            // effective, not configured -- selecting cloud without the SDK
            // or a key used to show "CLOUD BRAIN" while the local model
            // quietly answered
            window.backend = d.backend_effective || d.backend || "local";
            if (d.cloud_blocked)
                window.say("sys", "cloud mode is selected but " + d.cloud_blocked
                         + " — answering locally until that's fixed.");
            window.cloudModel = d.cloud_model || "claude-opus-5";
            window.autostart = d.autostart || ({});
            if (!window.autoApplied) { window.autoApplied = true;
                                       window.applyAutostart(); }
            window.capsVoice = d.voice || "";
            window.capsSpeak = !!d.speak;
            window.speakReplies = !!d.speak;
        } else if (d.t === "devices") {
            window.devicesData = d;
        } else if (d.t === "install_start") {
            window.choiceOpen = false;
            window.capBusy = d.cap || "";
            window.capPct = 0;
            window.capLabel = d.label || "installing";
            window.say("sys", "installing " + (d.engine || "") + "…");
        } else if (d.t === "install_progress") {
            window.capBusy = d.cap || window.capBusy;
            window.capPct = d.pct;
            window.capLabel = d.label || window.capLabel;
        } else if (d.t === "install_log") {
            if (window.consoleOpen) window.say("sys", d.v);
        } else if (d.t === "install_needs_terminal") {
            window.say("sys", "a terminal opened for the password prompt — "
                     + "finish there, then hit RESCAN.");
        } else if (d.t === "install_done") {
            window.sfx(d.ok ? "done" : "error");
            window.capBusy = ""; window.capPct = -1;
            window.say("sys", d.v || "install finished");
            if (d.ok) orb.pulse();
            window.flashSetup();
            window.capsRefresh();
        } else if (d.t === "connect_done") {
            window.say("sys", d.v || "");
            if (d.ok) orb.pulse(); else window.mode = "error";
            window.flashSetup();
            window.capsRefresh();
        } else if (d.t === "test_done") {
            window.say("sys", (d.ok ? "✓ " : "✗ ") + (d.v || ""));
            if (d.ok) orb.pulse();
        } else if (d.t === "engine_list") {
            // Do NOT round-trip this through the model. A 1.5B asked to "install
            // whisper-cpp" replies "microphone and speech connect for you" and
            // installs nothing. The options are known facts -- show them as
            // buttons that call the installer directly.
            window.settingsOpen = false;
            window.historyOpen = false;
            window.viewMode = "chat";
            choiceModel.clear();
            var list = (d.engines || []);
            for (var i = 0; i < list.length; i++) {
                var e = list[i];
                choiceModel.append({
                    cap: d.cap,
                    engine: e.engine,
                    ready: !!e.ready,
                    title: e.engine + (e.recommended ? "  · recommended" : ""),
                    sub: e.quality + "  ·  ~" + e.size_mb + " MB  ·  " + e.source,
                    desc: e.desc || ""
                });
            }
            window.choiceCap = d.cap;
            window.choiceTitle = d.label;
            window.say("tar", d.label + " has no engine installed. Pick one and "
                     + "I'll install it:");
            window.choiceOpen = true;
        } else if (d.t === "ok") {
            window.say("sys", d.v || "ok");
            window.flashSetup();
            window.capsRefresh();
            window.consoleRefresh();      // a saved key flips cloud rows to ready
        } else if (d.t === "error") {
            window.say("sys", "caps: " + (d.v || "failed"));
            window.capBusy = ""; window.capPct = -1;
        }
    }
    function consoleRefresh() {
        window.panelRun(["models"]);
        window.panelRun(["mem", "list"]);
    }

    function handlePanelLine(line) {
        line = (line || "").trim();
        if (line === "" || line.charAt(0) !== "{") return;
        var d;
        try { d = JSON.parse(line); } catch (e) { return; }

        if (d.t === "sessions") {
            sessionsModel.clear();
            var rows = d.rows || [];
            for (var i = 0; i < rows.length; i++) {
                sessionsModel.append(rows[i]);
                if (rows[i].current) window.currentSession = rows[i].id;
            }
            return;
        } else if (d.t === "session") {
            window.currentSession = d.id || window.currentSession;
            if (d.turns) {
                // replay the reopened conversation into the transcript
                chatModel.clear();
                for (var j = 0; j < d.turns.length; j++) {
                    window.say(d.turns[j].role === "user" ? "you" : "tar",
                               d.turns[j].content);
                }
            } else {
                chatModel.clear();
            }
            window.say("sys", d.v || "");
            return;
        } else if (d.t === "models") {
            modelsModel.clear();
            for (var i = 0; i < d.rows.length; i++) {
                var r = d.rows[i];
                // NB: a role literally called "model" collides with the
                // delegate's own `model` context object and resolves to
                // nothing. Rename on the way in.
                modelsModel.append({
                    name: String(r.model || ""),
                    tier: String(r.tier || ""),
                    lane: String(r.lane || ""),
                    sizeGb: Number(r.size_gb || 0),
                    installed: !!r.installed,
                    active: !!r.active,
                    desc: String(r.desc || "")
                });
            }
            window.activeModel = d.active || window.activeModel;
            window.activeLane = d.lane || window.activeLane;
            window.autotier = !!d.autotier;
        } else if (d.t === "mem_list") {
            memoryModel.clear();
            for (var j = 0; j < (d.facts || []).length; j++)
                memoryModel.append({ text: String(d.facts[j].text || "") });
        } else if (d.t === "probe") {
            window.activeModel = d.model || "";
            window.activeTier = d.tier || "";
            window.activeLane = d.lane || "";
            window.autotier = !!d.autotier;
            window.statusNote = d.reason || window.statusNote;
        } else if (d.t === "ok") {
            window.say("sys", d.v + (d.model ? "   \u2192   " + d.model : ""));
            window.panelRun(["probe"]);
            window.consoleRefresh();
            window.capsRefresh();     // backend may have flipped (cloud <-> local)
        } else if (d.t === "error") {
            window.say("sys", "ERR  " + d.v);
        }
    }

    // A CPU reply can take a minute. There was no way to abort it short of
    // closing the window.
    // ---- dictation -------------------------------------------------------
    // The mic capability could be installed and "ready" while nothing ever
    // recorded a single sample. This is the loop that was missing.
    property real micLevel: 0.0
    property bool listening: false
    Process {
        id: listenProc
        command: ["python3", Quickshell.env("HOME")
                  + "/.config/hypr/scripts/quickshell/tar/tar_listen.py",
                  "--seconds", "15", "--silence", "1.3"]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var d;
                try { d = JSON.parse(data); } catch (e) { return; }
                if (d.t === "level") {
                    window.micLevel = d.v;
                } else if (d.t === "listen") {
                    window.listening = true;
                    window.statusNote = d.v;
                    if (d.v === "recording") window.sfx("listen");
                } else if (d.t === "transcript") {
                    window.listening = false;
                    window.micLevel = 0;
                    if (d.ok && d.v) {
                        window.sfx("ok");
                        window.submit(d.v);
                    } else {
                        window.sfx("warn");
                        window.say("sys", d.v_reason || "didn't catch that");
                    }
                } else if (d.t === "error") {
                    window.listening = false;
                    window.micLevel = 0;
                    window.say("sys", d.v);
                }
            }
        }
        onExited: (c, st) => { window.listening = false; window.micLevel = 0; }
    }
    function listen() {
        if (listenProc.running) { listenProc.running = false; return; }
        var stt = window.capsData ? window.capsData.stt : undefined;
        if (!stt || !stt.ready) {
            window.say("sys", "no speech-to-text engine — open /settings.");
            return;
        }
        listenProc.running = true;
    }

    // ---- sound effects ---------------------------------------------------
    Process {
        id: sfxProc
        property string name: "ping"
        command: ["python3", Quickshell.env("HOME")
                  + "/.config/hypr/scripts/quickshell/tar/tar_sfx.py",
                  name, "0.45"]
    }
    function sfx(name) {
        if (!window.sfxEnabled) return;
        sfxProc.running = false;
        sfxProc.name = name;
        sfxProc.running = true;
    }
    property bool sfxEnabled: true

    // ---- test bay --------------------------------------------------------
    property string testMode: ""        // camera | mic | speaker | image
    property string testImage: ""
    property string testStatus: ""
    property bool testRunning: false
    property bool testHeard: false
    property real testPeak: 0.0

    function openTest(mode) {
        // deliberately does NOT touch viewMode -- opening the camera used to
        // yank you out of orb mode into chat for no reason
        window.testMode = mode;
        window.testStatus = "";
        window.testHeard = false;
        window.testPeak = 0;
        testPanel.levels = [];
        window.sfx("open");
        if (mode === "camera") { window.testRunning = true; camTick.start();
                                 window.grabFrame(); }
        else if (mode === "mic")     { window.startMicTest(); }
        else if (mode === "speaker") { window.testStatus =
                                       "press a sound below"; }
    }
    function closeTest() {
        camTick.stop();
        if (meterProc.running) meterProc.running = false;
        window.testRunning = false;
        window.testMode = "";
        window.sfx("close");
    }
    function showImage(path) {
        window.testMode = "image";
        window.testImage = path;
        window.testFrame++;
    }

    // live-ish camera: grab a still repeatedly. v4l2 can't be embedded in QML
    // directly, and a 1.4fps refresh is enough to aim a webcam.
    property int testFrame: 0
    Timer {
        id: camTick
        interval: 700
        repeat: true
        onTriggered: window.grabFrame()
    }
    Process {
        id: camProc
        property string dest: ""
        command: ["python3", window.toolsPath, "run", "cam_snap"]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var d; try { d = JSON.parse(data); } catch (e) { return; }
                if (d.t === "image" && d.path) {
                    window.testImage = d.path;
                    window.testFrame++;
                } else if (d.t === "acted" && d.v
                           && d.v.indexOf("failed") >= 0) {
                    window.testStatus = d.v;
                    camTick.stop();
                    window.testRunning = false;
                }
            }
        }
    }
    function grabFrame() {
        if (camProc.running) return;      // don't stack ffmpeg invocations
        camProc.running = true;
    }

    // mic meter
    Process {
        id: meterProc
        command: ["python3", Quickshell.env("HOME")
                  + "/.config/hypr/scripts/quickshell/tar/tar_meter.py",
                  "--seconds", "60"]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var d; try { d = JSON.parse(data); } catch (e) { return; }
                if (d.t === "level") {
                    testPanel.pushLevel(d.v);
                    window.testLevel(d.v);      // detached bay window
                    window.testPeak = d.peak;
                    if (d.peak > 0.06) window.testHeard = true;
                } else if (d.t === "meter_start") {
                    window.testStatus = "listening on "
                        + (d.device || "default").split(".").pop();
                } else if (d.t === "meter_done") {
                    window.testStatus = d.v;
                    window.testRunning = false;
                } else if (d.t === "error") {
                    window.testStatus = d.v;
                    window.testRunning = false;
                }
            }
        }
        onExited: (c, st) => window.testRunning = false
    }
    signal testLevel(real v)
    function stopMicTest() {
        if (meterProc.running) meterProc.running = false;
        window.testRunning = false;
    }
    function testSfx(name) {
        if (name === "__speak__") {
            window.speak("T.A.R. online. If you can hear this, your output "
                       + "device is correct.");
            window.testStatus = "spoke a line — heard it?";
        } else {
            window.sfx(name);
            window.testStatus = "played " + name + " — heard it?";
        }
    }
    function startMicTest() {
        window.testHeard = false;
        window.testRunning = true;
        meterProc.running = true;
    }

    readonly property string toolsPath:
        Quickshell.env("HOME") + "/.config/hypr/scripts/quickshell/tar/tar_tools.py"

    readonly property bool ttsReady:
        capsData && capsData.tts ? !!capsData.tts.ready : false
    function setSpeak(on) {
        window.speakReplies = on;
        window.capsRun(["speak", on ? "on" : "off"]);
    }
    function openSetupHint() {
        window.settingsOpen = true;
        settingsPane.toTop();
        window.capsRefresh();
        window.say("sys", "that needs a voice engine — pick one in setup.");
    }

    // Accepting a model upgrade: pin it, then re-send what failed.
    property string pendingRetry: ""
    Process {
        id: upgradeProc
        property string target: ""
        command: ["python3", window.brainPath, "use-model", target]
        onExited: (c, st) => {
            window.activeModel = upgradeProc.target;
            if (window.pendingRetry !== "") {
                var again = window.pendingRetry;
                window.pendingRetry = "";
                window.submit(again);
            }
        }
    }
    function upgradeModel(name) {
        upgradeProc.target = name;
        upgradeProc.running = true;
    }

    // ---- cloud brain ------------------------------------------------------
    Process {
        id: cloudProc
        property var argv: []
        command: argv
        stdout: SplitParser { splitMarker: "\n"; onRead: data => window.handleCapsLine(data) }
    }
    function cloudRun(args) {
        cloudProc.argv = ["python3", Quickshell.env("HOME")
            + "/.config/hypr/scripts/quickshell/tar/tar_cloud.py"].concat(args);
        cloudProc.running = true;
    }
    function setApiKey(k) {
        window.cloudRun(["set-key", k]);
        window.say("sys", "key saved — /cloud to switch the brain over.");
    }

    function stopGeneration() {
        if (!brain.running && !window.busy) return;
        brain.running = false;
        window.busy = false;
        window.streamIndex = -1;
        window.mode = "idle";
        window.statusNote = "stopped";
        orbView.settle();
        window.say("sys", "stopped.");
    }

    function run(args) {
        if (brain.running) brain.running = false;
        brain.argv = ["python3", window.brainPath].concat(args);
        window.busy = true;
        brain.running = true;
    }

    // --- speech out -----------------------------------------------------
    // No TTS engine is installed yet, so this routes through a helper that
    // picks whatever exists and tells us plainly when nothing does.
    property bool ttsMissingReported: false
    Process {
        id: tts
        property string say: ""
        command: ["bash", Quickshell.env("HOME")
                  + "/.config/hypr/scripts/quickshell/tar/tar_say.sh",
                  say, String(window.ttsVolume)]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                if (data.indexOf("NO_TTS") === 0 && !window.ttsMissingReported) {
                    window.ttsMissingReported = true;
                    window.say("sys", "no TTS engine installed — "
                             + "install piper-tts or espeak-ng to hear replies.");
                }
            }
        }
    }
    function speak(text) {
        if (!text) return;
        tts.running = false;
        tts.say = text;
        tts.running = true;
    }

    function submit(raw) {
        var text = (raw || "").trim();
        if (text === "" || window.busy) return;
        input.text = "";
        cmdModel.clear();

        // Slash commands ARE the settings. They don't print text -- they open
        // the console tab, which is where everything is actually changed.
        // (In orb mode there is no transcript, so a text reply would be invisible.)
        if (text.charAt(0) === "/") {
            var parts = text.substring(1).split(/\s+/);
            var cmd = (parts.shift() || "").toLowerCase();
            var arg = parts.join(" ").trim();

            // You preferred slash commands to a tab -- so setup IS one now.
            if (cmd === "cloud" || cmd === "api") {
                var want = (arg === "off" || arg === "local") ? "local" : "claude";
                window.capsRun(["backend", want]);
                window.say("sys", want === "claude"
                    ? "switching to the cloud brain…"
                    : "back to the local brain.");
                return;
            }
            if (cmd === "persona") {
                window.panelRun(arg ? ["persona"].concat(arg.split(/\s+/)) : ["persona"]);
                return;
            }
            if (cmd === "key") {
                if (!arg) { window.say("sys", "usage: /key <your-api-key>");
                            return; }
                window.setApiKey(arg);
                return;
            }
            if (cmd === "settings" || cmd === "setup") {
                window.viewMode = "chat";
                window.historyOpen = false;
                window.choiceOpen = false;
                window.settingsOpen = !window.settingsOpen;
                if (window.settingsOpen) window.capsRefresh();
                return;
            }
            if (cmd === "chats" || cmd === "history") { window.openHistory(); return; }
            if (cmd === "stop") { window.stopGeneration(); return; }
            if (cmd === "speak") {
                var on = arg !== "off";
                window.speakReplies = on;
                window.capsRun(["speak", on ? "on" : "off"]);
                return;
            }
            if (cmd === "new" || cmd === "newchat") { window.newSession(); return; }
            if (cmd === "clear") { chatModel.clear(); window.lastUser = "";
                                   window.lastReply = ""; return; }

            if (cmd === "model" && arg !== "") window.panelRun(["set-model", arg]);
            else if (cmd === "lane" && (arg === "open" || arg === "safe"))
                window.panelRun(["set-lane", arg]);
            else if (cmd === "auto") window.panelRun(["autotier", "on"]);
            else if (cmd === "forget" && arg !== "")
                window.panelRun(["mem", "forget", arg]);
            else if (cmd === "undo") window.panelRun(["undo"]);

            window.consoleOpen = true;
            window.consoleRefresh();
            return;
        }

        say("you", text);
        window.streamIndex = -1;
        run(["chat", "-m", text]);
    }

    // ------------------------------------------------------------------ visuals

    // Dim backdrop: the window is screen-sized, so without this the area
    // around the panel reads as a transparent hole punched in the desktop.
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.55 * window.frameAlpha)
    }

    // fullscreen shockwave rings, only alive during the intro
    Item {
        anchors.fill: parent
        visible: window.introBurst > 0.001
        z: 5

        Repeater {
            model: 4
            Rectangle {
                anchors.centerIn: parent
                readonly property real prog:
                    Math.max(0, Math.min(1, window.introBurst * 1.5 - index * 0.16))
                width: Math.max(1, prog * Math.max(window.width, window.height) * 1.5)
                height: width
                radius: width / 2
                color: "transparent"
                border.width: Math.max(1, 3 - index * 0.6)
                border.color: window.accent
                opacity: (1.0 - prog) * 0.5
                antialiasing: true
            }
        }
    }

    Item {
        id: frame
        anchors.centerIn: parent
        // slide left by half the group's extra width so the pair stays centred
        anchors.horizontalCenterOffset:
            (window.introDone && window.consoleOpen)
                ? -(window.consoleW + window.consoleGap) / 2 : 0
        Behavior on anchors.horizontalCenterOffset {
            NumberAnimation { duration: 360; easing.type: Easing.OutExpo }
        }

        width: window.introDone ? window.panelW : window.frameW
        Behavior on width {
            enabled: window.introDone
            NumberAnimation { duration: 360; easing.type: Easing.OutExpo }
        }
        height: window.frameH

    Rectangle {
        id: shell
        anchors.fill: parent
        color: Qt.rgba(theme.crust.r, theme.crust.g, theme.crust.b,
                       0.35 + window.frameAlpha * 0.65)
        clip: true

        // ---- layer 0: matrix rain -------------------------------------------
        TarRain {
            anchors.fill: parent
            visible: window.viewMode === "chat"
                     && !window.settingsOpen && !window.historyOpen
            running: visible
            glyphColor: window.accent
            headColor: theme.text
            columns: 18
            glyphSize: window.s(12)
            strength: (window.mode === "thinking" ? 1.9
                    : window.mode === "speaking" ? 1.35 : 1.0) * window.rainBoost * fx.rainBoost
            Behavior on strength { NumberAnimation { duration: 400 } }
        }

        // ---- scanline sweep (triggered by the `scan` ui action) --------------
        Rectangle {
            visible: window.viewMode === "chat" && window.scanY >= 0
                     && window.scanY <= 1
            width: parent.width
            height: window.s(64)
            y: window.scanY * parent.height - height / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5
                    color: Qt.rgba(window.accent.r, window.accent.g,
                                   window.accent.b, 0.22) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blurMax: 24; blur: 1.0 }
        }

        // ---- layer 1: vignette ----------------------------------------------
        Rectangle {
            anchors.fill: parent
            visible: window.viewMode === "chat"
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.0) }
                GradientStop { position: 0.55; color: Qt.rgba(0, 0, 0, 0.18) }
                GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.42) }
            }
        }

        // ---- layer 2: accent bloom behind the orb ---------------------------
        Rectangle {
            visible: window.viewMode === "chat"
                     && !window.settingsOpen && !window.historyOpen
            width: parent.width * 0.55
            height: width
            radius: width / 2
            x: -width * 0.18
            y: -height * 0.42
            color: window.accent
            opacity: 0.09
            antialiasing: true
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blurMax: 64; blur: 1.0 }
            Behavior on color { ColorAnimation { duration: 500 } }
        }

        // ---- content ---------------------------------------------------------
        // The test bay sits to the LEFT of the chat column, as its own panel,
        // so a camera feed or a live waveform is visible next to the
        // conversation instead of collapsed into one line of text.
        RowLayout {
            anchors.fill: parent
            anchors.margins: window.s(20)
            spacing: window.s(12)
            visible: window.viewMode === "chat"
            enabled: window.viewMode === "chat"

            // Slides out beside the chat, like the setup pane. Width animates
            // so it reads as a drawer opening rather than the chat suddenly
            // losing half its space.
            TarTestPanel {
                id: testPanel
                Layout.preferredWidth: window.testMode !== "" ? window.s(380) : 0
                Layout.fillHeight: true
                visible: Layout.preferredWidth > 1
                clip: true
                opacity: window.testMode !== "" ? 1.0 : 0.0
                Behavior on Layout.preferredWidth {
                    NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
                }
                Behavior on opacity { NumberAnimation { duration: 180 } }
                theme: theme
                accent: window.accent
                scaleFn: window.s
                mode: window.testMode
                imagePath: window.testImage
                frameTick: window.testFrame
                micPeak: window.testPeak
                micHeard: window.testHeard
                running: window.testRunning
                statusText: window.testStatus

                onClosed: window.closeTest()
                onGrabFrame: window.grabFrame()
                onStartMic: window.startMicTest()
                onStopMic: {
                    if (meterProc.running) meterProc.running = false;
                    window.testRunning = false;
                }
                onPlaySfx: name => {
                    if (name === "__speak__") {
                        window.speak("T.A.R. online. If you can hear this, "
                                   + "your output device is correct.");
                        window.testStatus = "spoke a line — heard it?";
                    } else {
                        window.sfx(name);
                        window.testStatus = "played " + name + " — heard it?";
                    }
                }
            }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: window.s(12)

            // =========================================================== header
            RowLayout {
                Layout.fillWidth: true
                spacing: window.s(16)

                // orb in a bracketed housing
                Item {
                    Layout.preferredWidth: window.s(78)
                    Layout.preferredHeight: window.s(78)

                    TarOrb {
                        id: orb
                        anchors.fill: parent
                        coreSize: window.s(50)
                        mode: window.mode
                        level: window.listening
                   ? Math.max(window.orbLevel, window.micLevel)
                   : window.orbLevel
                    }

                    // corner brackets around the orb
                    Repeater {
                        model: 4
                        Item {
                            readonly property bool rightSide: index === 1 || index === 2
                            readonly property bool bottomSide: index >= 2
                            width: window.s(11); height: window.s(11)
                            x: rightSide ? parent.width - width : 0
                            y: bottomSide ? parent.height - height : 0

                            Rectangle {
                                width: parent.width; height: 1.5
                                color: window.accent; opacity: 0.85
                                y: parent.bottomSide ? parent.height - height : 0
                            }
                            Rectangle {
                                width: 1.5; height: parent.height
                                color: window.accent; opacity: 0.85
                                x: parent.rightSide ? parent.width - width : 0
                            }
                        }
                    }
                }

                // wordmark block
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: window.s(3)

                    TarGlitch {
                        text: "T.A.R."
                        color: theme.text
                        pixelSize: window.s(30)
                        bold: true
                        letterSpacing: window.s(4)
                        // tears harder while it is working
                        intensity: window.mode === "thinking" ? 0.85
                                 : window.mode === "error" ? 1.4 : 0.0
                        burstInterval: window.mode === "thinking" ? 700 : 3200
                        Behavior on intensity { NumberAnimation { duration: 260 } }
                    }

                    Text {
                        text: window.brandLine
                        color: Qt.rgba(theme.overlay1.r, theme.overlay1.g, theme.overlay1.b, 0.9)
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(9)
                        font.letterSpacing: window.s(2)
                    }

                    // status readout with a live pip
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: window.s(3)
                        spacing: window.s(7)

                        Rectangle {
                            width: window.s(6); height: window.s(6); radius: width / 2
                            color: window.accent
                            SequentialAnimation on opacity {
                                loops: Animation.Infinite; running: true
                                NumberAnimation { to: 0.25; duration: 780; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 1.0; duration: 780; easing.type: Easing.InOutSine }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: window.statusNote
                            color: window.mode === "error" ? theme.red : theme.subtext0
                            font.family: "JetBrains Mono"
                            font.pixelSize: window.s(10)
                            elide: Text.ElideRight
                        }
                    }
                }

                // model readout, HUD style
                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    spacing: window.s(2)
                    visible: window.activeModel !== ""

                    Text {
                        Layout.alignment: Qt.AlignRight
                        text: "[ " + (window.activeTier || "?").toUpperCase() + " ]"
                        color: window.accent
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(10)
                        font.letterSpacing: window.s(1)
                    }
                    Text {
                        Layout.alignment: Qt.AlignRight
                        text: window.activeModel
                        color: theme.subtext1
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(9)
                    }
                    Text {
                        Layout.alignment: Qt.AlignRight
                        // In cloud mode the local "unrestricted" lane is
                        // meaningless -- say which brain is answering instead.
                        text: window.cloudMode ? "// CLOUD BRAIN"
                            : window.activeLane === "open" ? "// UNRESTRICTED"
                                                           : "// STOCK"
                        color: window.activeLane === "open" ? theme.peach : theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(9)
                        font.letterSpacing: window.s(1)
                    }
                }
            }

            // ====================================================== header bar
            // Controls live up here now, grouped by what they do, instead of
            // one undifferentiated row jammed against the text field:
            //   left  = modes you switch between   (chat / orb / chats)
            //   right = panels and the way out     (console, setup, close)
            // The volume slider is hidden until the cursor is over the audio
            // pair, and SPEAK/READ are the same setting, so they stay in sync.
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: window.s(6)
                spacing: window.s(6)

                TarHudButton {
                    theme: theme; accent: window.accent; scaleFn: window.s
                    glyph: "\u{f0e2b}"; label: "ORB"
                    onClicked: window.viewMode = "orb"
                }
                TarHudButton {
                    theme: theme; accent: window.accent; scaleFn: window.s
                    glyph: "\u{f054c}"; label: "CHATS"
                    active: window.historyOpen
                    onClicked: window.historyOpen
                        ? window.historyOpen = false : window.openHistory()
                }

                // ---- audio pair: hover either one to reveal the slider
                Item {
                    id: audioGroup
                    Layout.preferredWidth: audioRow.implicitWidth
                    Layout.preferredHeight: window.s(30)
                    property bool hovered: audioHover.hovered || volHover.hovered

                    Row {
                        id: audioRow
                        spacing: window.s(6)
                        TarHudButton {
                            theme: theme; accent: window.accent; scaleFn: window.s
                            glyph: window.speakReplies ? "\u{f075a}" : "\u{f075f}"
                            label: window.speakReplies ? "SPEAKS" : "SILENT"
                            active: window.speakReplies
                            locked: !window.ttsReady
                            onClicked: window.setSpeak(!window.speakReplies)
                            onLockedClicked: window.openSetupHint()
                        }
                        TarHudButton {
                            theme: theme; accent: window.accent; scaleFn: window.s
                            glyph: "\u{f0f5d}"; label: "READ"
                            locked: !window.ttsReady
                            enabled: window.lastReply !== ""
                            onClicked: window.speak(window.lastReply)
                            onLockedClicked: window.openSetupHint()
                        }
                    }
                    HoverHandler { id: audioHover }

                    // floats ABOVE the pair, only while hovered
                    Rectangle {
                        y: -height - window.s(6)
                        x: 0
                        width: window.s(130)
                        height: window.s(26)
                        visible: audioGroup.hovered
                        color: Qt.rgba(theme.crust.r, theme.crust.g,
                                       theme.crust.b, 0.95)
                        border.width: 1
                        border.color: Qt.rgba(window.accent.r, window.accent.g,
                                              window.accent.b, 0.5)
                        HoverHandler { id: volHover }

                        Row {
                            anchors.centerIn: parent
                            spacing: window.s(6)
                            Text {
                                text: "VOL"
                                color: theme.overlay1
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(8)
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Item {
                                width: window.s(78); height: window.s(14)
                                anchors.verticalCenter: parent.verticalCenter
                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: parent.width; height: window.s(2)
                                    color: Qt.rgba(theme.surface1.r,
                                                   theme.surface1.g,
                                                   theme.surface1.b, 0.8)
                                }
                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: parent.width * window.ttsVolume
                                    height: window.s(2)
                                    color: window.accent
                                }
                                Rectangle {
                                    x: parent.width * window.ttsVolume - width / 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: window.s(4); height: window.s(12)
                                    color: window.accent
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onPositionChanged: (m) => {
                                        if (pressed) window.ttsVolume =
                                            Math.max(0, Math.min(1,
                                                m.x / parent.width));
                                    }
                                    onClicked: (m) => window.ttsVolume =
                                        Math.max(0, Math.min(1, m.x / parent.width))
                                }
                            }
                            Text {
                                text: Math.round(window.ttsVolume * 100)
                                color: theme.subtext0
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(8)
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }

                Item { Layout.fillWidth: true }

                TarHudButton {
                    theme: theme; accent: window.accent; scaleFn: window.s
                    glyph: "\u{f0a9e}"; label: "CONSOLE"
                    active: window.consoleOpen
                    onClicked: window.consoleOpen = !window.consoleOpen
                }
                TarHudButton {
                    theme: theme; accent: window.accent; scaleFn: window.s
                    glyph: "\u{f0493}"; label: "SETUP"
                    active: window.settingsOpen
                    onClicked: {
                        window.settingsOpen = !window.settingsOpen;
                        if (window.settingsOpen) window.capsRefresh();
                    }
                }
                TarHudButton {
                    theme: theme; accent: theme.red; scaleFn: window.s
                    glyph: "\u{f0156}"
                    // Says what it will actually do: with a pane open it backs
                    // out of the pane, and only closes T.A.R. when there is
                    // nothing left to back out of.
                    label: window.anyPaneOpen ? "BACK" : "CLOSE"
                    onClicked: window.escapeLayer()
                }
            }

            // divider with a sweeping scan highlight
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 1

                Rectangle {
                    anchors.fill: parent
                    color: Qt.rgba(theme.surface1.r, theme.surface1.g, theme.surface1.b, 0.55)
                }
                Rectangle {
                    id: scanDot
                    width: parent.width * 0.22
                    height: 1
                    color: window.accent
                    opacity: 0.9
                    SequentialAnimation on x {
                        loops: Animation.Infinite; running: true
                        NumberAnimation { from: -scanDot.width; to: scanDot.parent.width
                                          duration: 4200; easing.type: Easing.InOutSine }
                        PauseAnimation { duration: 1600 }
                    }
                }
            }

            // ======================================================== setup pane
            // Takes the transcript's slot rather than floating over it, so the
            // input bar stays reachable and nothing is hidden behind it.
            TarSettings {
                id: settingsPane
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: window.settingsOpen && !window.historyOpen
                theme: theme
                accent: window.accent
                scaleFn: window.s

                caps: window.capsData
                devices: window.devicesData
                autostart: window.autostart
                startMode: window.startMode
                voice: window.capsVoice
                speakOn: window.capsSpeak
                busyCap: window.capBusy
                busyPct: window.capPct
                busyLabel: window.capLabel

                onRefresh: { window.flashSetup(); window.capsRefresh(); }
                onSetAutostart: (key, on) =>
                    window.capsRun(["autostart", key, on ? "on" : "off"])
                onSetStartMode: mode => window.capsRun(["start-mode", mode])
                onConnectCap: cap => window.installRun(["connect", cap])
                onChooseEngine: (cap, engine) => window.capsRun(["set-engine", cap, engine])
                onChooseDevice: (kind, id) => window.capsRun(["set-device", kind, id])
                onTestCap: cap => {
                    // A one-line "ok" in the chat is indistinguishable from
                    // nothing happening. Open the real thing instead.
                    if (cap === "camera")      window.openTest("camera");
                    else if (cap === "stt")    window.openTest("mic");
                    else if (cap === "tts")    window.openTest("speaker");
                    else window.capsRun(["test", cap]);
                }
                onToggleSpeak: window.capsRun(["speak", window.capsSpeak ? "off" : "on"])
                // "ASK T.A.R." -> pull the real option list, then let
                // handleCapsLine() drop the question into the context bar.
                onAskInstall: cap => window.capsRun(["engines", cap])
            }

            // ====================================================== chat history
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: window.historyOpen
                spacing: window.s(10)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: window.s(10)
                    Text {
                        text: "CONVERSATIONS"
                        color: theme.text
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(13)
                        font.letterSpacing: window.s(3)
                        font.bold: true
                    }
                    Rectangle {
                        Layout.fillWidth: true; height: 1
                        color: Qt.rgba(window.accent.r, window.accent.g,
                                       window.accent.b, 0.35)
                    }
                    TarHudButton {
                        theme: theme; accent: window.accent; scaleFn: window.s
                        glyph: "\u{f0415}"; label: "NEW"
                        onClicked: window.newSession()
                    }
                    TarHudButton {
                        theme: theme; accent: window.accent; scaleFn: window.s
                        glyph: "\u{f0156}"; label: "BACK"
                        onClicked: window.historyOpen = false
                    }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: window.s(5)
                    model: sessionsModel
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        width: ListView.view.width
                        implicitHeight: window.s(42)
                        color: model.current
                            ? Qt.rgba(window.accent.r, window.accent.g,
                                      window.accent.b, 0.14)
                            : Qt.rgba(theme.mantle.r, theme.mantle.g,
                                      theme.mantle.b, 0.45)
                        border.width: 1
                        border.color: model.current
                            ? Qt.rgba(window.accent.r, window.accent.g,
                                      window.accent.b, 0.5)
                            : Qt.rgba(theme.surface1.r, theme.surface1.g,
                                      theme.surface1.b, 0.45)

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: window.s(9)
                            spacing: window.s(10)
                            Text {
                                text: model.current ? "\u{f0450}" : "\u{f0542}"
                                color: model.current ? window.accent : theme.overlay1
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(13)
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text {
                                    Layout.fillWidth: true
                                    text: model.title
                                    color: theme.text
                                    elide: Text.ElideRight
                                    font.family: "JetBrains Mono"
                                    font.pixelSize: window.s(11)
                                }
                                Text {
                                    text: model.id + "   ·   " + model.turns
                                          + " msg" + (model.turns === 1 ? "" : "s")
                                    color: theme.overlay1
                                    font.family: "JetBrains Mono"
                                    font.pixelSize: window.s(9)
                                }
                            }
                            Text {
                                visible: model.current
                                text: "CURRENT"
                                color: window.accent
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(8)
                                font.letterSpacing: window.s(1)
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: window.loadSession(model.id)
                        }
                    }
                }
            }

            // ======================================================= transcript
            ListView {
                id: transcript
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !window.settingsOpen && !window.historyOpen
                clip: true
                spacing: window.s(14)
                model: chatModel
                boundsBehavior: Flickable.StopAtBounds
                // lay out every message, not just the visible ones, so the end
                // position is real rather than an estimate
                cacheBuffer: 100000

                // content grows AFTER a delegate is created (and while a reply
                // streams in), so re-pin whenever it changes
                onContentHeightChanged: if (window.pinBottom) Qt.callLater(window.pinNow)
                onHeightChanged: if (window.pinBottom) Qt.callLater(window.pinNow)
                onCountChanged: window.scrollToEnd()
                // scrolling up breaks the pin; scrolling back to the end restores it
                onMovementEnded: window.pinBottom = atYEnd
                onFlickEnded: window.pinBottom = atYEnd
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: RowLayout {
                    width: transcript.width
                    spacing: window.s(10)

                    // left accent rail
                    Rectangle {
                        Layout.preferredWidth: model.who === "sys" ? 1 : window.s(2)
                        Layout.fillHeight: true
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: window.s(2)
                        implicitHeight: bodyCol.implicitHeight
                        color: model.who === "you" ? theme.mauve
                             : model.who === "sys" ? Qt.rgba(theme.surface2.r, theme.surface2.g, theme.surface2.b, 0.7)
                             : model.who === "act" ? theme.green
                             : window.accent
                        opacity: model.who === "sys" ? 0.5 : 0.85
                    }

                    ColumnLayout {
                        id: bodyCol
                        Layout.fillWidth: true
                        spacing: window.s(3)

                        Text {
                            text: model.who === "you" ? "YOU"
                                : model.who === "sys" ? "SYS"
                                : model.who === "act" ? "\u2713 ACTED" : "T.A.R."
                            color: model.who === "you" ? theme.mauve
                                 : model.who === "sys" ? theme.overlay0
                                 : model.who === "act" ? theme.green : window.accent
                            font.family: "JetBrains Mono"
                            font.pixelSize: window.s(9)
                            font.letterSpacing: window.s(2)
                            font.bold: true
                        }

                        Text {
                            Layout.fillWidth: true
                            text: model.text
                                + (index === window.streamIndex ? "▊" : "")
                            color: model.who === "sys" ? theme.subtext0
                                 : model.who === "act" ? theme.subtext1 : theme.text
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            font.family: "JetBrains Mono"
                            font.pixelSize: window.s(model.who === "sys" ? 11
                                                    : model.who === "act" ? 11 : 13)
                            lineHeight: 1.3
                        }
                    }
                }

                // idle splash
                Column {
                    anchors.centerIn: parent
                    visible: chatModel.count === 0
                    spacing: window.s(8)

                    TarGlitch {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "// OFFLINE  ·  LOCAL  ·  YOURS"
                        color: theme.overlay0
                        pixelSize: window.s(12)
                        letterSpacing: window.s(2)
                        burstInterval: 3800
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "/help for commands"
                        color: Qt.rgba(theme.overlay0.r, theme.overlay0.g, theme.overlay0.b, 0.75)
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(10)
                    }
                }
            }

            // ================================================== command palette
            ColumnLayout {
                Layout.fillWidth: true
                visible: window.cmdOpen
                spacing: 0

                Repeater {
                    model: cmdModel
                    delegate: Rectangle {
                        required property int index
                        required property var model
                        Layout.fillWidth: true
                        implicitHeight: window.s(30)
                        color: index === window.cmdIndex
                            ? Qt.rgba(window.accent.r, window.accent.g,
                                      window.accent.b, 0.20)
                            : Qt.rgba(theme.mantle.r, theme.mantle.g,
                                      theme.mantle.b, 0.75)

                        Rectangle {
                            width: window.s(2); height: parent.height
                            color: window.accent
                            visible: index === window.cmdIndex
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: window.s(12)
                            anchors.rightMargin: window.s(12)
                            spacing: window.s(10)

                            Text {
                                text: "/" + model.cmd
                                color: index === window.cmdIndex
                                       ? window.accent : theme.text
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(11)
                                font.bold: index === window.cmdIndex
                            }
                            Text {
                                text: model.args
                                color: theme.overlay1
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(10)
                            }
                            Item { Layout.fillWidth: true }
                            Text {
                                text: model.desc
                                color: theme.subtext0
                                elide: Text.ElideRight
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(10)
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onEntered: window.cmdIndex = index
                            onClicked: window.applyCommand(index)
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: window.s(20)
                    color: Qt.rgba(theme.crust.r, theme.crust.g,
                                   theme.crust.b, 0.85)
                    Text {
                        anchors.centerIn: parent
                        text: "\u2191\u2193 choose   \u21b9 complete   "
                            + "\u21b5 run   esc dismiss"
                        color: theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(9)
                    }
                }
            }

            // =========================================================== choices
            // Inline decision strip: when T.A.R. needs the user to pick
            // something (which TTS voice, which STT engine), it offers real
            // buttons here instead of hoping a 1.5B model parses the answer.
            ColumnLayout {
                Layout.fillWidth: true
                visible: window.choiceOpen && choiceModel.count > 0
                spacing: window.s(5)

                Repeater {
                    model: choiceModel
                    delegate: Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: window.s(46)
                        color: hov.hovered
                            ? Qt.rgba(window.accent.r, window.accent.g,
                                      window.accent.b, 0.18)
                            : Qt.rgba(theme.mantle.r, theme.mantle.g,
                                      theme.mantle.b, 0.6)
                        border.width: 1
                        border.color: model.ready
                            ? Qt.rgba(theme.green.r, theme.green.g,
                                      theme.green.b, 0.5)
                            : Qt.rgba(window.accent.r, window.accent.g,
                                      window.accent.b, 0.45)
                        Behavior on color { ColorAnimation { duration: 120 } }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: window.s(9)
                            spacing: window.s(10)

                            Text {
                                text: model.ready ? "\u{f012c}" : "\u{f0193}"
                                color: model.ready ? theme.green : window.accent
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(14)
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text {
                                    text: model.title
                                    color: theme.text
                                    font.family: "JetBrains Mono"
                                    font.pixelSize: window.s(11)
                                    font.bold: true
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: model.sub + "  ·  " + model.desc
                                    color: theme.subtext0
                                    elide: Text.ElideRight
                                    font.family: "JetBrains Mono"
                                    font.pixelSize: window.s(9)
                                }
                            }
                            Text {
                                text: model.ready ? "USE" : "INSTALL"
                                color: model.ready ? theme.green : window.accent
                                font.family: "JetBrains Mono"
                                font.pixelSize: window.s(9)
                                font.letterSpacing: window.s(1.5)
                                font.bold: true
                            }
                        }
                        HoverHandler { id: hov }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                window.choiceOpen = false;
                                if (model.cap === "__model__") {
                                    if (model.engine === "__keep__") {
                                        window.say("sys", "staying on the "
                                                 + "current model.");
                                    } else {
                                        window.say("act", "switching to "
                                                 + model.engine);
                                        window.upgradeModel(model.engine);
                                    }
                                    return;
                                }
                                if (model.ready) {
                                    window.capsRun(["set-engine", model.cap,
                                                    model.engine]);
                                    window.say("act", "using " + model.engine);
                                } else {
                                    window.say("act", "installing " + model.engine
                                             + "\u2026");
                                    window.installRun(["install", model.cap,
                                                       model.engine]);
                                }
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "or just say what you want \u2014 cancel with Esc"
                    color: theme.overlay0
                    font.family: "JetBrains Mono"
                    font.pixelSize: window.s(9)
                }
            }

            // ============================================================ input
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: window.s(46)
                color: Qt.rgba(theme.mantle.r, theme.mantle.g, theme.mantle.b, 0.72)
                border.width: 1
                border.color: input.activeFocus
                    ? Qt.rgba(window.accent.r, window.accent.g, window.accent.b, 0.7)
                    : Qt.rgba(theme.surface1.r, theme.surface1.g, theme.surface1.b, 0.5)
                Behavior on border.color { ColorAnimation { duration: 150 } }

                // top-left + bottom-right corner ticks
                Rectangle { width: window.s(9); height: 1.5; color: window.accent; x: -1; y: -1 }
                Rectangle { width: 1.5; height: window.s(9); color: window.accent; x: -1; y: -1 }
                Rectangle { width: window.s(9); height: 1.5; color: window.accent
                            x: parent.width - width + 1; y: parent.height - 0.5 }
                Rectangle { width: 1.5; height: window.s(9); color: window.accent
                            x: parent.width - 0.5; y: parent.height - height + 1 }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: window.s(14)
                    anchors.rightMargin: window.s(14)
                    spacing: window.s(9)

                    Text {
                        text: ">"
                        color: window.busy ? theme.peach : window.accent
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(15)
                        font.bold: true
                    }

                    TextField {
                        id: input
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        background: Item {}
                        color: theme.text
                        font.family: "JetBrains Mono"
                        font.pixelSize: window.s(13)
                        placeholderText: window.busy ? "" : "ask_"
                        placeholderTextColor: theme.overlay0
                        verticalAlignment: TextInput.AlignVCenter
                        enabled: !window.busy
                        focus: true

                        // live filter as the command is typed
                        onTextChanged: window.refreshCommands(text)

                        Keys.onUpPressed: (event) => {
                            if (window.cmdOpen) {
                                window.cmdIndex = (window.cmdIndex - 1
                                    + cmdModel.count) % cmdModel.count;
                                event.accepted = true;
                            }
                        }
                        Keys.onDownPressed: (event) => {
                            if (window.cmdOpen) {
                                window.cmdIndex =
                                    (window.cmdIndex + 1) % cmdModel.count;
                                event.accepted = true;
                            }
                        }
                        Keys.onTabPressed: (event) => {
                            if (window.cmdOpen) {
                                var c = cmdModel.get(window.cmdIndex);
                                input.text = "/" + c.cmd
                                           + (c.args !== "" ? " " : "");
                                input.cursorPosition = input.text.length;
                                cmdModel.clear();
                                event.accepted = true;
                            }
                        }
                        Keys.onReturnPressed: (event) => {
                            if (window.cmdOpen) {
                                window.applyCommand(window.cmdIndex);
                            } else {
                                window.submit(text);
                            }
                            event.accepted = true;
                        }
                        Keys.onEscapePressed: (event) => {
                            if (window.cmdOpen) { cmdModel.clear(); }
                            else { window.escapeLayer(); }
                            event.accepted = true;
                        }
                    }

                    // Only STOP lives on the input bar now -- it is the one
                    // control you need at the exact moment you are typing.
                    // Everything else moved to the header, out of the way.
                    TarHudButton {
                        Layout.alignment: Qt.AlignVCenter
                        visible: window.busy
                        theme: theme
                        accent: theme.red
                        scaleFn: window.s
                        glyph: "\u{f04db}"
                        label: "STOP"
                        onClicked: window.stopGeneration()
                    }


                    // working indicator: a small bar meter
                    Row {
                        spacing: 2
                        visible: window.busy
                        Repeater {
                            model: 5
                            Rectangle {
                                width: window.s(3)
                                height: window.s(13)
                                color: window.accent
                                SequentialAnimation on opacity {
                                    loops: Animation.Infinite
                                    running: window.busy
                                    PauseAnimation { duration: index * 90 }
                                    NumberAnimation { to: 1.0; duration: 240 }
                                    NumberAnimation { to: 0.18; duration: 240 }
                                    PauseAnimation { duration: (4 - index) * 90 }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ---- ORB MODE --------------------------------------------------------
        }

        TarOrbView {
            id: orbView
            anchors.fill: parent
            rainBoost: window.rainBoost * fx.rainBoost
            visible: window.viewMode === "orb"
            enabled: window.viewMode === "orb"

            theme: theme
            hostScale: window.s
            mode: window.mode
            level: window.orbLevel
            busy: window.busy
            lastUser: window.lastUser
            lastReply: window.lastReply
            statusNote: window.statusNote
            activeModel: window.activeModel
            activeTier: window.activeTier
            activeLane: window.activeLane
            micEnabled: window.micEnabled
            speakReplies: window.speakReplies
            sttReady: window.capsData && window.capsData.stt
                      ? !!window.capsData.stt.ready : false
            ttsReady: window.capsData && window.capsData.tts
                      ? !!window.capsData.tts.ready : false
            volume: window.ttsVolume

            onSubmitted: (text) => window.submit(text)
            onMicToggled: (on) => {
                var stt = window.capsData ? window.capsData.stt : undefined;
                if (on && (!stt || stt.locked)) {
                    // don't pretend: send them where it can actually be fixed
                    window.micEnabled = false;
                    window.say("sys", "no speech-to-text engine installed — "
                             + "opening setup.");
                    window.viewMode = "chat";
                    window.settingsOpen = true;
                    window.capsRefresh();
                    return;
                }
                window.micEnabled = on;
                if (on) window.listen();
                else if (listenProc.running) listenProc.running = false;
            }
            onSpeakToggled: (on) => {
                window.speakReplies = on;
                window.capsRun(["speak", on ? "on" : "off"]);
            }
            onVolumeRequested: (v) => window.ttsVolume = v
            onReadAloudRequested: (text) => window.speak(text)
            onChatModeRequested: window.viewMode = "chat"
            bootAllowed: window.introDone
            consoleOpen: window.consoleOpen
            onConsoleRequested: window.consoleOpen = !window.consoleOpen
            // Setup and past chats are now reachable without leaving orb mode.
            onSetupRequested: {
                window.viewMode = "chat";
                window.settingsOpen = true;
                window.historyOpen = false;
                window.capsRefresh();
                window.say("sys", "that needs an engine — hit ASK T.A.R. on the "
                         + "locked card and pick one.");
            }
            onHistoryRequested: window.openHistory()
            onCloseRequested: window.close()
            onStopRequested: window.stopGeneration()
        }

        // ---- layer 9: scanlines over everything (chat only -- over the orb
        // they flatten the sphere into a striped disc) ------------------------
        Canvas {
            anchors.fill: parent
            visible: window.viewMode === "chat"
            opacity: 0.16
            renderStrategy: Canvas.Cooperative
            onPaint: {
                // painted once; static overlay, no per-frame cost
                var ctx = getContext("2d");
                ctx.clearRect(0, 0, width, height);
                ctx.fillStyle = "#000000";
                for (var y = 0; y < height; y += 3) ctx.fillRect(0, y, width, 1);
            }
        }

        // outer frame + corner brackets
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(window.accent.r, window.accent.g, window.accent.b, 0.30)
        }
        Repeater {
            model: 4
            Item {
                readonly property bool rightSide: index === 1 || index === 2
                readonly property bool bottomSide: index >= 2
                width: window.s(20); height: window.s(20)
                x: rightSide ? shell.width - width : 0
                y: bottomSide ? shell.height - height : 0

                Rectangle {
                    width: parent.width; height: 2
                    color: window.accent
                    y: parent.bottomSide ? parent.height - height : 0
                }
                Rectangle {
                    width: 2; height: parent.height
                    color: window.accent
                    x: parent.rightSide ? parent.width - width : 0
                }
            }
        }
    }
    }

        // ---- CONSOLE ---------------------------------------------------------
    TarConsole {
        id: consolePanel
        open: window.consoleOpen && window.introDone

        // docked to the outside edge of the panel, not over it
        anchors.left: frame.right
        anchors.leftMargin: window.s(10)
        anchors.top: frame.top
        anchors.topMargin: window.s(6)
        height: frame.height - window.s(12)
        theme: theme
        scaleFn: window.s
        accent: window.accent

        modelsModel: modelsModel
        memoryModel: memoryModel
        activeModel: window.activeModel
        activeTier: window.activeTier
        activeLane: window.activeLane
        autotier: window.autotier
        micEnabled: window.micEnabled
        speakReplies: window.speakReplies
        volume: window.ttsVolume
        hwNote: window.statusNote
        cloudOn: window.cloudMode
        cloudModel: window.cloudModel

        onRefreshRequested: window.consoleRefresh()
        onPinModel: (name) => window.panelRun(["set-model", name])
        onSetLane: (lane) => window.panelRun(["set-lane", lane])
        onSetAuto: window.panelRun(["autotier", "on"])
        onForgetFact: (t) => window.panelRun(["mem", "forget", t])
        onMicToggled: (on) => window.micEnabled = on
        onSpeakToggled: (on) => window.speakReplies = on
        onVolumeRequested: (v) => window.ttsVolume = v
        onUndoRequested: window.panelRun(["undo"])
        onClosed: window.consoleOpen = false
        onSetBackend: (which) => window.capsRun(["backend", which])
        onSetKey: (k) => window.setApiKey(k)
    }

    // ---- effects layer: on top of BOTH views, so effects are always visible
    TarFx {
        id: fx
        theme: theme
        accent: window.accent
        scaleFn: window.s
    }
}
