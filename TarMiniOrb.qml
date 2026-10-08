// T.A.R.'s mini orb.
//
// HOME: while T.A.R. runs, a small orb sits in the bottom-right corner.
//   left-click  -> a text box slides out; Enter sends it to T.A.R.
//   right-click -> open the full T.A.R. window
//   its bubble shows "thinking…" and then the reply (click it for the full chat)
// WORKING: when T.A.R. clicks/types on screen, the orb leaves home, glides to
//   just BESIDE each spot before it's clicked (ring flash on the click), says
//   what it's doing in a speech bubble, and flies home when it's done.
//
// Working mode is driven by $TAR_DATA/mini.json, written by tar_tools.py (so the
// chat brain AND background tasks move it). Python owns that layout, and the
// same boxes -- plus the home orb's box, published to mini-dock.json -- are
// blacked out of T.A.R.'s screenshots, so it never sees or clicks itself.
// The working overlay is click-through (empty input mask); home takes input
// only on the orb, the text box and the bubble.
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick

Scope {
    id: mini

    property var host: null                 // tar.qml root: submit(), summon(), busy, lastReply
    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    property var st: ({})
    property bool on: false                 // working mode (Python drives it)
    property int lastSeq: -1
    property int lastClick: -1
    property bool flash: false

    readonly property int homeMargin: 26

    MatugenColors { id: theme }

    function screenFor(name) {
        var all = Quickshell.screens;
        for (var i = 0; i < all.length; i++)
            if (all[i].name === name) return all[i];
        return all.length ? all[0] : null;
    }

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
                mini.on = !!d.on && fresh && d.x !== undefined;
                if (mini.on) { idle.interval = hold * 1000; idle.restart(); }
            } catch (e) {}
        }
    }
    // a rename-replaced file can drop an inotify watch, so poll (tiny local read)
    Timer {
        interval: mini.on ? 70 : 150
        running: true
        repeat: true
        onTriggered: feed.reload()
    }
    Timer { id: idle; interval: 6000; onTriggered: mini.on = false }

    // where the home orb is, for T.A.R.'s screenshot masking
    FileView {
        id: dockOut
        path: mini.dataDir + "/mini-dock.json"
        printErrors: false
    }
    function publishDock(visible, x, y, w, h) {
        dockOut.setText(JSON.stringify({ visible: visible, x: x, y: y, w: w, h: h,
                                         at: Date.now() / 1000 }));
    }

    // ================================================================ HOME
    PanelWindow {
        id: dock
        visible: !mini.on
        screen: mini.screenFor("")
        anchors { bottom: true; right: true }
        margins { bottom: mini.homeMargin; right: mini.homeMargin }
        exclusionMode: ExclusionMode.Ignore
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "tar-mini-dock"
        WlrLayershell.keyboardFocus: dock.typing ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        implicitWidth: 380
        implicitHeight: 190
        // only the orb, the text box and the bubble take clicks; the rest of
        // this box is see-through AND click-through
        mask: Region {
            item: homeOrb
            Region { item: field.visible ? field : null }
            Region { item: homeBubble.visible ? homeBubble : null }
        }

        property bool typing: false
        property bool hover: homeArea.containsMouse
        readonly property bool busy: !!(mini.host && mini.host.busy)
        property string reply: ""
        property bool showReply: false

        onVisibleChanged: dock.publish()
        Component.onCompleted: dock.publish()
        function publish() {
            var s = dock.screen;
            var sw = s ? s.width : 1920, sh = s ? s.height : 1080;
            mini.publishDock(dock.visible, sw - mini.homeMargin - dock.implicitWidth,
                             sh - mini.homeMargin - dock.implicitHeight,
                             dock.implicitWidth, dock.implicitHeight);
        }

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
        }
        Timer { id: replyTimer; interval: 14000; onTriggered: dock.showReply = false }

        function send() {
            var t = input.text.trim();
            if (t === "" || !mini.host) return;
            mini.host.submit(t);
            input.text = "";
            dock.typing = false;
            dock.showReply = false;
        }

        // ---- the orb
        Item {
            id: homeOrb
            width: 56; height: 56
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            scale: dock.hover ? 1.08 : 1.0
            Behavior on scale { NumberAnimation { duration: 140 } }

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
                    running: dock.visible && (dock.busy || dock.hover || dock.typing)
                }
            }
            Rectangle {     // core
                anchors.centerIn: parent
                width: 22 + 4 * homePulse.k; height: width; radius: width / 2
                color: theme.mauve
                border.width: 1
                border.color: theme.text
            }
            Item {
                id: homePulse
                visible: false
                property real k: 0
                SequentialAnimation on k {
                    loops: Animation.Infinite
                    running: dock.visible && dock.busy
                    NumberAnimation { to: 1; duration: 500; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0; duration: 500; easing.type: Easing.InOutSine }
                }
            }
            MouseArea {
                id: homeArea
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: Qt.PointingHandCursor
                onClicked: (m) => {
                    if (m.button === Qt.RightButton) {
                        if (mini.host) mini.host.summon();
                        return;
                    }
                    dock.typing = !dock.typing;
                    if (dock.typing) { dock.showReply = false; focusLater.start(); }
                }
            }
        }
        Timer { id: focusLater; interval: 60; onTriggered: input.forceActiveFocus() }

        // ---- the text box (slides out to the left of the orb)
        Rectangle {
            id: field
            visible: dock.typing
            anchors.right: homeOrb.left
            anchors.rightMargin: 8
            anchors.verticalCenter: homeOrb.verticalCenter
            width: 300; height: 38
            radius: 19
            color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.96)
            border.width: 1
            border.color: theme.mauve
            TextInput {
                id: input
                anchors.fill: parent
                anchors.leftMargin: 16; anchors.rightMargin: 16
                verticalAlignment: TextInput.AlignVCenter
                color: theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: 12
                clip: true
                Keys.onReturnPressed: dock.send()
                Keys.onEnterPressed: dock.send()
                Keys.onEscapePressed: dock.typing = false
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: input.text === ""
                    text: "ask T.A.R.…"
                    color: theme.overlay1
                    font: input.font
                }
            }
        }

        // ---- speech bubble above the orb: thinking… / the reply
        Rectangle {
            id: homeBubble
            visible: !dock.typing && (dock.busy || (dock.showReply && dock.reply !== ""))
            anchors.right: parent.right
            anchors.bottom: homeOrb.top
            anchors.bottomMargin: 10
            width: Math.min(340, said.implicitWidth + 26)
            height: Math.min(120, said.implicitHeight + 18)
            radius: 12
            color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.95)
            border.width: 1
            border.color: theme.mauve
            Text {
                id: said
                anchors.centerIn: parent
                width: Math.min(implicitWidth, 314)
                text: dock.busy ? "thinking…" : dock.reply
                color: theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: 11
                wrapMode: Text.Wrap
                maximumLineCount: 6
                elide: Text.ElideRight
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
                onClicked: { dock.showReply = false; if (mini.host) mini.host.summon(); }
            }
        }
    }

    // ============================================================= WORKING
    LazyLoader {
        active: mini.on

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
            readonly property bool thinking: (mini.st.say || "").length > 0
            // fly out from home on the first frame, then follow Python
            property bool launched: false
            Timer { interval: 30; running: true; onTriggered: panel.launched = true }

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
                x: (panel.launched ? (mini.st.x || 0) : panel.width - mini.homeMargin - 28) - width / 2
                y: (panel.launched ? (mini.st.y || 0) : panel.height - mini.homeMargin - 28) - height / 2
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

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

            // cartoon speech bubble (box decided by Python, away from the target)
            Item {
                id: bubble
                visible: panel.thinking && orb.visible
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
                        maximumLineCount: 3
                        elide: Text.ElideRight
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
