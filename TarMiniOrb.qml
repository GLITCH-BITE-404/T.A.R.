// T.A.R.'s mini orb.
//
// HOME: while T.A.R. runs, a small orb sits on screen (bottom-right until you
//   drag it somewhere else -- the spot is remembered).
//   click       -> the card: T.A.R.'s last reply in full, what it's doing now,
//                  every background task (pause / stop), and a box to type in
//   right-click -> open the full T.A.R. window
//   drag        -> move it
//   its bubble says "thinking…" / a short reply and fades away on its own
// WORKING: when T.A.R. clicks/types on screen, a worker orb flies out from home,
//   glides to just BESIDE each spot before it's clicked (ring flash on the
//   click), says what it's doing in a few words, and flies home when done.
//
// Working mode is driven by $TAR_DATA/mini.json, written by tar_tools.py (so the
// chat brain AND background tasks move it). Python owns that layout, and the
// same boxes -- plus the home orb's, published to mini-dock.json -- are blacked
// out of T.A.R.'s screenshots, so it never sees or clicks itself. The worker is
// click-through (empty input mask); home takes input only where it draws.
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick

Scope {
    id: mini

    property var host: null                 // tar.qml root: submit(), summon(), busy, lastReply
    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    readonly property string tools: Quickshell.env("HOME")
        + "/.config/hypr/scripts/quickshell/tar/tar_tools.py"
    property var st: ({})
    property bool on: false                 // working mode (Python drives it)
    property bool leaving: false            // worker flying home before it vanishes
    property int lastSeq: -1
    property int lastClick: -1
    property bool flash: false
    property var task: null                 // loop.json while a background task runs

    // home position = distance of the orb's box from the bottom-right corner
    property int homeRight: 26
    property int homeBottom: 26
    readonly property int boxW: 400
    readonly property int boxH: 520

    MatugenColors { id: theme }

    function screenFor(name) {
        var all = Quickshell.screens;
        for (var i = 0; i < all.length; i++)
            if (all[i].name === name) return all[i];
        return all.length ? all[0] : null;
    }
    function act(name) {
        Quickshell.execDetached(["python3", mini.tools, "run", name]);
    }

    // ---- working-mode feed from Python
    FileView {
        id: feed
        path: mini.dataDir + "/mini.json"
        printErrors: false
        onLoaded: {
            try {
                var d = JSON.parse(text());
                if (d.seq === mini.lastSeq) return;
                mini.lastSeq = d.seq;
                var hold = d.hold || 6;
                var fresh = (Date.now() / 1000 - (d.at || 0)) < hold;
                if (d.click !== undefined && d.click !== mini.lastClick) {
                    if (mini.lastClick >= 0 && fresh) mini.flash = !mini.flash;
                    mini.lastClick = d.click;
                }
                mini.st = d;
                mini.setOn(!!d.on && fresh && d.x !== undefined);
                if (mini.on) { idle.interval = hold * 1000; idle.restart(); }
            } catch (e) {}
        }
    }
    function setOn(v) {
        if (v === mini.on) return;
        if (!v) { mini.leaving = true; leave.restart(); }
        else { mini.leaving = false; leave.stop(); }
        mini.on = v;
    }
    Timer { id: leave; interval: 700; onTriggered: mini.leaving = false }
    // a rename-replaced file can drop an inotify watch, so poll (tiny local read)
    Timer {
        interval: mini.on ? 70 : 150
        running: true
        repeat: true
        onTriggered: feed.reload()
    }
    Timer { id: idle; interval: 6000; onTriggered: mini.setOn(false) }

    // ---- background tasks (same file the TASKS tab reads)
    FileView {
        id: loopFile
        path: mini.dataDir + "/loop.json"
        printErrors: false
        onLoaded: { try { mini.task = JSON.parse(text()); } catch (e) { mini.task = null; } }
        onLoadFailed: mini.task = null
    }
    Timer {
        interval: dock.cardOpen ? 1000 : 3000
        running: true
        repeat: true
        onTriggered: loopFile.reload()
    }

    // ---- remembered home spot
    FileView {
        id: posFile
        path: mini.dataDir + "/mini-pos.json"
        printErrors: false
        onLoaded: {
            try {
                var p = JSON.parse(text());
                // the old window-moving drag could save spots off the screen
                var sc = mini.screenFor("");
                var sw = sc ? sc.width : 1920, sh = sc ? sc.height : 1080;
                if (p.right !== undefined) mini.homeRight = Math.max(4, Math.min(sw - 60, p.right));
                if (p.bottom !== undefined) mini.homeBottom = Math.max(4, Math.min(sh - 60, p.bottom));
            } catch (e) {}
        }
    }
    function savePos() {
        posFile.setText(JSON.stringify({ right: mini.homeRight, bottom: mini.homeBottom }));
    }

    // where the home orb is drawn, for T.A.R.'s screenshot masking
    FileView {
        id: dockOut
        path: mini.dataDir + "/mini-dock.json"
        printErrors: false
    }

    // ================================================================ HOME
    // The window covers the whole screen (see-through, and click-through
    // everywhere except the orb/card/bubble) and the orb moves INSIDE it.
    // Moving the window itself made dragging jumpy: the compositor applies
    // each move a frame late, so the cursor's local position kept overshooting.
    PanelWindow {
        id: dock
        screen: mini.screenFor("")
        anchors { top: true; bottom: true; left: true; right: true }
        exclusionMode: ExclusionMode.Ignore
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "tar-mini-dock"
        WlrLayershell.keyboardFocus: dock.cardOpen ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        // only what's drawn takes clicks; everything else is click-through
        mask: Region {
            item: homeOrb
            Region { item: card.visible ? card : null }
            Region { item: homeBubble.opacity > 0.05 ? homeBubble : null }
        }

        property bool cardOpen: false
        property bool hover: homeArea.containsMouse
        readonly property bool busy: !!(mini.host && mini.host.busy)
        readonly property bool taskOn: !!(mini.task && mini.task.pid)
        property string reply: ""
        property string asked: ""
        property bool showReply: false

        Connections {
            target: mini.host
            ignoreUnknownSignals: true
            function onLastReplyChanged() {
                var r = mini.host.lastReply || "";
                if (r === "") return;
                dock.reply = r;
                dock.showReply = true;
                replyTimer.restart();
            }
            // a new question wipes the old answer -- it must never look like
            // the reply to what you just asked
            function onLastUserChanged() {
                if (!mini.host.lastUser) return;
                dock.asked = mini.host.lastUser;
                dock.reply = "";
                dock.showReply = false;
            }
            function onBusyChanged() {
                if (mini.host.busy) dock.showReply = false;
            }
        }
        Timer { id: replyTimer; interval: 9000; onTriggered: dock.showReply = false }

        function send() {
            var t = input.text.trim();
            if (t === "" || !mini.host) return;
            dock.asked = t;
            dock.reply = "";
            dock.showReply = false;
            mini.host.submit(t);
            input.text = "";
        }

        // ---- masking box (screen coords) -- re-published when anything moves
        function publish() {
            var ox = home.x, oy = home.y;
            var x1 = homeOrb.x, y1 = homeOrb.y, x2 = homeOrb.x + homeOrb.width, y2 = homeOrb.y + homeOrb.height;
            var extra = card.visible ? card : (homeBubble.opacity > 0.05 ? homeBubble : null);
            if (extra) {
                x1 = Math.min(x1, extra.x); y1 = Math.min(y1, extra.y);
                x2 = Math.max(x2, extra.x + extra.width); y2 = Math.max(y2, extra.y + extra.height);
            }
            dockOut.setText(JSON.stringify({ visible: true, x: Math.round(ox + x1), y: Math.round(oy + y1),
                                             w: Math.round(x2 - x1), h: Math.round(y2 - y1),
                                             at: Date.now() / 1000 }));
        }
        Timer { id: pubLater; interval: 120; onTriggered: dock.publish() }
        onCardOpenChanged: pubLater.restart()
        Component.onCompleted: { posFile.reload(); pubLater.restart(); }
        Connections {
            target: mini
            function onHomeRightChanged() { pubLater.restart(); }
            function onHomeBottomChanged() { pubLater.restart(); }
        }

        Item {
        id: home
        width: mini.boxW
        height: mini.boxH
        x: dock.width - mini.homeRight - mini.boxW
        y: dock.height - mini.homeBottom - mini.boxH

        // ---- the orb (click = card, right-click = full T.A.R., drag = move)
        Item {
            id: homeOrb
            width: 56; height: 56
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            scale: dock.hover ? 1.08 : 1.0
            opacity: mini.on ? 0.55 : 1.0          // dimmer while its worker is out
            Behavior on scale { NumberAnimation { duration: 140 } }
            Behavior on opacity { NumberAnimation { duration: 400 } }

            Rectangle {     // glow
                anchors.centerIn: parent
                width: 54; height: 54; radius: 27
                color: theme.mauve
                opacity: 0.16 + 0.12 * homePulse.k
            }
            Rectangle {     // ring
                anchors.centerIn: parent
                width: 42; height: 42; radius: 21
                color: "transparent"
                border.width: 2
                border.color: Qt.rgba(theme.mauve.r, theme.mauve.g, theme.mauve.b, 0.6)
                Rectangle { width: 7; height: 7; radius: 3.5; color: theme.text
                            x: parent.width / 2 - 3.5; y: -2.5 }
                // only spins while something happens: an idle orb costs no frames
                RotationAnimation on rotation {
                    from: 0; to: 360; duration: dock.busy ? 1200 : 2600
                    loops: Animation.Infinite
                    running: dock.busy || dock.taskOn || dock.hover || dock.cardOpen
                }
            }
            Rectangle {     // core
                anchors.centerIn: parent
                width: 22 + 4 * homePulse.k; height: width; radius: width / 2
                color: theme.mauve
                border.width: 1
                border.color: theme.text
            }
            Rectangle {     // task badge
                visible: dock.taskOn
                width: 12; height: 12; radius: 6
                anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 4
                color: (mini.task && mini.task.paused) ? theme.yellow : theme.green
                border.width: 1; border.color: theme.base
            }
            Item {
                id: homePulse
                visible: false
                property real k: 0
                SequentialAnimation on k {
                    loops: Animation.Infinite
                    running: dock.busy
                    NumberAnimation { to: 1; duration: 500; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0; duration: 500; easing.type: Easing.InOutSine }
                }
            }
            MouseArea {
                id: homeArea
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: drag ? Qt.ClosedHandCursor : Qt.PointingHandCursor
                property real px: 0           // press point, in screen (dock) coords
                property real py: 0
                property real r0: 0
                property real b0: 0
                property bool drag: false
                onPressed: (m) => {
                    var g = mapToItem(dock.contentItem, m.x, m.y);
                    px = g.x; py = g.y; r0 = mini.homeRight; b0 = mini.homeBottom; drag = false;
                }
                onPositionChanged: (m) => {
                    if (!pressed || m.buttons !== Qt.LeftButton) return;
                    var g = mapToItem(dock.contentItem, m.x, m.y);
                    var dx = g.x - px, dy = g.y - py;
                    if (!drag && Math.abs(dx) + Math.abs(dy) < 5) return;
                    drag = true;
                    // keep the orb itself on screen (the box around it may hang off)
                    // the orb is the box's bottom-right corner: keep IT on screen
                    mini.homeRight = Math.max(4, Math.min(dock.width - 60, r0 - dx));
                    mini.homeBottom = Math.max(4, Math.min(dock.height - 60, b0 - dy));
                }
                onReleased: { if (drag) mini.savePos(); }
                onClicked: (m) => {
                    if (drag) return;
                    if (m.button === Qt.RightButton) {
                        if (mini.host) mini.host.summon();
                        return;
                    }
                    dock.cardOpen = !dock.cardOpen;
                    if (dock.cardOpen) { dock.showReply = false; focusLater.start(); }
                }
            }
        }
        Timer { id: focusLater; interval: 60; onTriggered: input.forceActiveFocus() }

        // ---- the card: full reply, what it's doing, tasks, and a text box
        Rectangle {
            id: card
            visible: dock.cardOpen
            opacity: dock.cardOpen ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 220 } }
            anchors.right: parent.right
            anchors.bottom: homeOrb.top
            anchors.bottomMargin: 10
            width: 360
            height: Math.min(mini.boxH - 70, col.implicitHeight + 24)
            radius: 14
            color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.97)
            border.width: 1
            border.color: theme.mauve
            Keys.onEscapePressed: dock.cardOpen = false

            Column {
                id: col
                x: 12; y: 12
                width: parent.width - 24
                spacing: 10

                // header
                Row {
                    spacing: 8
                    Text { text: "T.A.R."; color: theme.mauve; font.family: "JetBrains Mono"
                           font.pixelSize: 11; font.bold: true; font.letterSpacing: 2 }
                    Text { text: dock.busy ? "· thinking…" : (mini.on ? "· working" : "· ready")
                           color: theme.overlay1; font.family: "JetBrains Mono"; font.pixelSize: 10
                           anchors.verticalCenter: parent.verticalCenter }
                }

                // doing right now (worker orb's line)
                Text {
                    visible: mini.on && (mini.st.say || "") !== ""
                    width: parent.width
                    text: "▸ " + (mini.st.say || "")
                    color: theme.text; font.family: "JetBrains Mono"; font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                // last exchange, in full
                Text {
                    visible: dock.asked !== ""
                    width: parent.width
                    text: "you: " + dock.asked
                    color: theme.overlay1; font.family: "JetBrains Mono"; font.pixelSize: 10
                    wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                }
                Text {
                    visible: dock.reply === "" && dock.busy
                    text: "thinking…"
                    color: theme.overlay1; font.family: "JetBrains Mono"; font.pixelSize: 11
                }
                Flickable {
                    visible: dock.reply !== ""
                    width: parent.width
                    height: Math.min(full.implicitHeight, 180)
                    contentHeight: full.implicitHeight
                    clip: true
                    Text {
                        id: full
                        width: parent.width
                        text: dock.reply
                        color: theme.text; font.family: "JetBrains Mono"; font.pixelSize: 11
                        wrapMode: Text.Wrap
                        lineHeight: 1.2
                    }
                }

                // background tasks
                Rectangle {
                    visible: dock.taskOn
                    width: parent.width
                    height: taskCol.implicitHeight + 16
                    radius: 10
                    color: Qt.rgba(theme.mauve.r, theme.mauve.g, theme.mauve.b, 0.08)
                    border.width: 1
                    border.color: Qt.rgba(theme.mauve.r, theme.mauve.g, theme.mauve.b, 0.35)
                    Column {
                        id: taskCol
                        x: 8; y: 8
                        width: parent.width - 16
                        spacing: 6
                        Text {
                            width: parent.width
                            text: "TASK  " + ((mini.task && mini.task.task) || "")
                            color: theme.text; font.family: "JetBrains Mono"; font.pixelSize: 10
                            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                        }
                        Text {
                            width: parent.width
                            text: mini.task ? ((mini.task.paused ? "paused" : (mini.task.status || ""))
                                  + "  ·  round " + (mini.task.round || 0)
                                  + (mini.task.clicks ? "  ·  " + mini.task.clicks + " clicks" : "")
                                  + (mini.task.until ? "  ·  " + Math.max(0, Math.round(
                                        (mini.task.until - Date.now() / 1000) / 60)) + " min left" : ""))
                                  : ""
                            color: theme.overlay1; font.family: "JetBrains Mono"; font.pixelSize: 9
                            wrapMode: Text.Wrap
                        }
                        Text {
                            visible: !!(mini.task && mini.task.last)
                            width: parent.width
                            text: "last: " + ((mini.task && mini.task.last) || "")
                            color: theme.overlay1; font.family: "JetBrains Mono"; font.pixelSize: 9
                            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                        }
                        Row {
                            spacing: 8
                            Repeater {
                                model: [
                                    { label: (mini.task && mini.task.paused) ? "RESUME" : "PAUSE",
                                      run: (mini.task && mini.task.paused) ? "resume_task" : "pause_task" },
                                    { label: "STOP", run: "stop_task" }
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    width: lbl.implicitWidth + 18; height: 22; radius: 11
                                    color: btnArea.containsMouse
                                        ? Qt.rgba(theme.mauve.r, theme.mauve.g, theme.mauve.b, 0.25) : "transparent"
                                    border.width: 1; border.color: theme.mauve
                                    Text { id: lbl; anchors.centerIn: parent; text: modelData.label
                                           color: theme.text; font.family: "JetBrains Mono"
                                           font.pixelSize: 9; font.bold: true }
                                    MouseArea { id: btnArea; anchors.fill: parent; hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: { mini.act(modelData.run); loopFile.reload(); } }
                                }
                            }
                        }
                    }
                }

                // type to T.A.R.
                Rectangle {
                    width: parent.width
                    height: 36
                    radius: 18
                    color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.9)
                    border.width: 1
                    border.color: input.activeFocus ? theme.mauve : theme.overlay1
                    TextInput {
                        id: input
                        anchors.fill: parent
                        anchors.leftMargin: 14; anchors.rightMargin: 14
                        verticalAlignment: TextInput.AlignVCenter
                        color: theme.text
                        font.family: "JetBrains Mono"
                        font.pixelSize: 12
                        clip: true
                        Keys.onReturnPressed: dock.send()
                        Keys.onEnterPressed: dock.send()
                        Keys.onEscapePressed: dock.cardOpen = false
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: input.text === ""
                            text: dock.busy ? "thinking…" : "ask T.A.R.…"
                            color: theme.overlay1
                            font: input.font
                        }
                    }
                }
            }
        }

        // ---- small speech bubble above the orb (fades in and out slowly)
        Rectangle {
            id: homeBubble
            readonly property bool want: !dock.cardOpen && !mini.on
                && (dock.busy || (dock.showReply && dock.reply !== ""))
            opacity: want ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: homeBubble.want ? 350 : 1400; easing.type: Easing.InOutQuad } }
            onOpacityChanged: if (opacity === 0 || opacity === 1) pubLater.restart()
            anchors.right: parent.right
            anchors.bottom: homeOrb.top
            anchors.bottomMargin: 10
            width: Math.min(300, said.implicitWidth + 26)
            height: said.implicitHeight + 18
            radius: 12
            color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.95)
            border.width: 1
            border.color: theme.mauve
            Text {
                id: said
                anchors.centerIn: parent
                width: Math.min(implicitWidth, 274)
                // short: the full reply is one click away in the card
                text: dock.busy ? "thinking…" : dock.shortReply(dock.reply)
                color: theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: 11
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
                Behavior on text {
                    SequentialAnimation {
                        NumberAnimation { target: said; property: "opacity"; to: 0; duration: 180 }
                        PropertyAction {}
                        NumberAnimation { target: said; property: "opacity"; to: 1; duration: 450 }
                    }
                }
            }
            Rectangle {     // tail
                width: 10; height: 10; rotation: 45
                color: homeBubble.color
                border.width: 1; border.color: theme.mauve
                x: parent.width - 28 - 5; y: parent.height - 5
                z: -1
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: { dock.cardOpen = true; focusLater.start(); }
            }
        }
        }   // home

        function shortReply(r) {
            var t = String(r || "").replace(/[*_`#>]+/g, "").trim();
            var first = (t.split(/\n/)[0].match(/^.*?[.!?](?=\s|$)/) || [t.split(/\n/)[0]])[0];
            var w = first.split(/\s+/);
            return w.length > 12 ? w.slice(0, 12).join(" ") + "…" : first;
        }
    }

    // ============================================================= WORKING
    LazyLoader {
        active: mini.on || mini.leaving

        PanelWindow {
            id: panel
            screen: mini.screenFor(mini.st.mon)
            anchors { top: true; bottom: true; left: true; right: true }
            exclusionMode: ExclusionMode.Ignore
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            WlrLayershell.namespace: "tar-mini"
            mask: Region {}                 // empty input region = clicks pass through

            readonly property color accent: theme.mauve
            readonly property bool thinking: (mini.st.say || "").length > 0 && mini.on
            // home orb centre, in this overlay's coordinates
            readonly property real homeX: panel.width - mini.homeRight - 28
            readonly property real homeY: panel.height - mini.homeBottom - 28
            // fly out from home on the first frame, then follow Python
            property bool launched: false
            Timer { interval: 30; running: true; onTriggered: panel.launched = true }
            readonly property bool atHome: !panel.launched || !mini.on

            // click flash at the real target spot
            Rectangle {
                id: ring
                property real k: 0
                x: (mini.st.tx || 0) - width / 2
                y: (mini.st.ty || 0) - height / 2
                width: 14 + 46 * k; height: width; radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: panel.accent
                opacity: 1 - k
                visible: k > 0 && k < 1
                NumberAnimation on k { id: ringAnim; from: 0; to: 1; duration: 420; running: false
                                       easing.type: Easing.OutCubic }
                Connections {
                    target: mini
                    function onFlashChanged() { ringAnim.restart(); }
                }
            }

            Item {
                id: orb
                visible: mini.st.x !== undefined
                width: 40; height: 40
                x: (panel.atHome ? panel.homeX : (mini.st.x || 0)) - width / 2
                y: (panel.atHome ? panel.homeY : (mini.st.y || 0)) - height / 2
                opacity: mini.on ? 1 : 0
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: 650 } }

                Rectangle {     // glow
                    anchors.centerIn: parent
                    width: 50; height: 50; radius: 25
                    color: panel.accent
                    opacity: 0.18 + 0.1 * pulse.k
                }
                Rectangle {     // spinning ring
                    anchors.centerIn: parent
                    width: 38; height: 38; radius: 19
                    color: "transparent"
                    border.width: 2
                    border.color: Qt.rgba(panel.accent.r, panel.accent.g, panel.accent.b, 0.55)
                    Rectangle { width: 8; height: 8; radius: 4; color: theme.text
                                x: parent.width / 2 - 4; y: -3 }
                    RotationAnimation on rotation {
                        from: 0; to: 360; duration: panel.thinking ? 1400 : 3200
                        loops: Animation.Infinite; running: mini.on
                    }
                }
                Rectangle {     // core
                    anchors.centerIn: parent
                    width: 20 + 4 * pulse.k; height: width; radius: width / 2
                    color: panel.accent
                    border.width: 1
                    border.color: theme.text
                }
                Item {
                    id: pulse
                    visible: false
                    property real k: 0
                    SequentialAnimation on k {
                        loops: Animation.Infinite; running: mini.on
                        NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 0; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
            }

            // speech bubble (box decided by Python, away from the target);
            // fades in, and fades out slowly
            Item {
                id: bubble
                opacity: panel.thinking && orb.visible ? 1 : 0
                visible: opacity > 0.01
                Behavior on opacity { NumberAnimation { duration: panel.thinking ? 350 : 1200 } }
                x: mini.st.bx || 0
                y: mini.st.by || 0
                width: 264; height: 72
                readonly property bool below: (mini.st.by || 0) > (mini.st.y || 0)
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

                Rectangle {
                    id: box
                    width: Math.min(264, said2.implicitWidth + 24)
                    height: Math.min(72, said2.implicitHeight + 16)
                    x: Math.max(0, Math.min(264 - width, (mini.st.x || 0) - bubble.x - width / 2))
                    y: bubble.below ? 0 : 72 - height
                    radius: 12
                    color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.94)
                    border.width: 1
                    border.color: panel.accent
                    Text {
                        id: said2
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, 240)
                        text: mini.st.say || ""
                        color: theme.text
                        font.family: "JetBrains Mono"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        Behavior on text {
                            SequentialAnimation {
                                NumberAnimation { target: said2; property: "opacity"; to: 0; duration: 160 }
                                PropertyAction {}
                                NumberAnimation { target: said2; property: "opacity"; to: 1; duration: 420 }
                            }
                        }
                    }
                }
                Rectangle {     // tail pointing at the orb
                    width: 10; height: 10
                    rotation: 45
                    color: box.color
                    border.width: 1
                    border.color: panel.accent
                    x: Math.max(box.x + 10, Math.min(box.x + box.width - 20,
                                                     (mini.st.x || 0) - bubble.x - 5))
                    y: bubble.below ? -5 : 72 - 5
                    z: -1
                }
            }
        }
    }
}
