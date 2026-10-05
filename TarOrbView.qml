import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Effects
import "."

// T.A.R. orb mode -- the orb IS the interface.
//
// Boot order is deliberate: the orb runs its transformation alone, oversized,
// against an empty field. Only once it settles does the rig shrink to working
// size and the HUD fade in around it. Everything lives close to the ball.
Item {
    id: view

    // ---- injected state -----------------------------------------------------
    property var theme
    property var hostScale: null
    // capability gating, so dead controls look dead
    property bool sttReady: false
    property bool ttsReady: false
    function s(v) { return hostScale ? hostScale(v) : v }

    property string mode: "boot"
    property real level: 0.0
    property real rainBoost: 1.0          // effects layer can flood the rain
    property bool busy: false
    property string lastUser: ""
    property string lastReply: ""
    property string statusNote: ""

    // Transient notices. In orb mode there is no transcript, so anything T.A.R.
    // reported -- an action, a warning, an install finishing -- happened
    // invisibly. These surface it above the orb and fade out on their own.
    property string toastText: ""
    property string toastKind: "sys"      // sys | act | error
    property string activeModel: ""
    property string activeTier: ""
    property string activeLane: ""

    property bool micEnabled: false
    property bool speakReplies: false
    property real volume: 0.7

    // ---- outbound -----------------------------------------------------------
    signal submitted(string text)
    // shared draft + voice input (same as chat mode)
    property string draft: ""
    signal draftEdited(string text)
    property bool hearing: false
    property bool hearBusy: false
    property string hearLabel: ""
    property var hearLevels: []
    signal voiceStart()
    signal voiceStop()
    signal voiceCancel()
    onDraftChanged: if (orbInput.text !== draft) orbInput.text = draft
    signal micToggled(bool on)
    signal speakToggled(bool on)
    signal volumeRequested(real v)
    signal readAloudRequested(string text)
    signal chatModeRequested()
    signal consoleRequested()
    signal tasksRequested()
    signal setupRequested()      // a LOCKED feature was clicked
    signal settingsRequested()   // plain SETUP button
    signal historyRequested()
    signal closeRequested()
    signal stopRequested()
    property bool consoleOpen: false
    property bool tasksOpen: false
    property bool tasksActive: false

    readonly property color accent: mode === "error" ? theme.red
                                  : mode === "thinking" ? theme.blue
                                  : mode === "listening" ? theme.teal
                                  : theme.mauve

    // ======================================================== boot / reveal
    // revealed drives every HUD element's opacity + slide-in.
    property bool bootAllowed: false
    property bool booted: false
    property real revealed: 0.0
    // rig starts oversized and settles -- the "it was fullscreen, now it's a
    // console" move.
    property real rigScale: 1.34

    function tryBoot() {
        if (mode !== "boot" && bootAllowed && !booted) {
            booted = true;
            orb.boot();
            revealSeq.start();
        }
    }
    onModeChanged: tryBoot()
    onBootAllowedChanged: tryBoot()

    SequentialAnimation {
        id: revealSeq
        // orb performs alone for a beat
        PauseAnimation { duration: 1500 }
        ParallelAnimation {
            NumberAnimation { target: view; property: "rigScale"
                              to: 1.0; duration: 1100; easing.type: Easing.OutExpo }
            NumberAnimation { target: view; property: "revealed"
                              to: 1.0; duration: 900; easing.type: Easing.OutCubic }
        }
    }

    // ---- choreography -------------------------------------------------------
    property string phase: "idle"      // idle|swallow|orbit|reply
    property string inFlight: ""

    function beginSwallow(text) {
        inFlight = text; phase = "swallow"; swallowAnim.restart();
    }
    function surfaceReply() {
        if (phase === "orbit") { phase = "reply"; replyAnim.restart(); }
    }
    function settle() { phase = "idle"; }

    property real swallowProgress: 0.0
    property real orbitSpin: 0.0
    property real replyRise: 0.0

    SequentialAnimation {
        id: swallowAnim
        NumberAnimation { target: view; property: "swallowProgress"
                          from: 0.0; to: 1.0; duration: 640
                          easing.type: Easing.InBack; easing.overshoot: 1.2 }
        ScriptAction { script: { orb.pulse(); view.phase = "orbit"; } }
        PropertyAction { target: view; property: "swallowProgress"; value: 0.0 }
    }

    NumberAnimation on orbitSpin {
        running: view.phase === "orbit"
        loops: Animation.Infinite
        from: 0; to: 360; duration: 3000
    }

    NumberAnimation {
        id: replyAnim
        target: view; property: "replyRise"
        from: 0.0; to: 1.0; duration: 540; easing.type: Easing.OutCubic
    }

    // ============================================================= backdrop
    // Scanlines live BEHIND the orb here -- drawn over it they flattened the
    // sphere into a striped disc.
    Canvas {
        anchors.fill: parent
        opacity: 0.13
        renderStrategy: Canvas.Cooperative
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = "#000000";
            for (var y = 0; y < height; y += 3) ctx.fillRect(0, y, width, 1);
        }
    }

    TarRain {
        anchors.fill: parent
        glyphColor: view.accent
        headColor: view.theme.text
        columns: 20
        glyphSize: view.s(12)
        strength: (view.mode === "thinking" ? 1.4 : 0.7) * view.rainBoost
                  * (0.35 + view.revealed * 0.65)
        Behavior on strength { NumberAnimation { duration: 400 } }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.12) }
            GradientStop { position: 0.5; color: Qt.rgba(0, 0, 0, 0.26) }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.50) }
        }
    }

    // ============================================================== the rig
    Item {
        id: rig
        anchors.centerIn: parent
        // the ball is the hero: over half the short edge
        width: Math.min(view.width, view.height) * 0.56
        height: width
        scale: view.rigScale

        // --- concentric arc rings ------------------------------------------
        Repeater {
            model: [
                { r: 0.60, span: 128, off:   0, w: 2.0, dir:  1, dur: 15000, op: 0.80 },
                { r: 0.60, span:  46, off: 180, w: 2.0, dir:  1, dur: 15000, op: 0.80 },
                { r: 0.71, span:  70, off:  40, w: 1.4, dir: -1, dur: 23000, op: 0.55 },
                { r: 0.71, span:  70, off: 220, w: 1.4, dir: -1, dur: 23000, op: 0.55 },
                { r: 0.83, span: 190, off: 120, w: 1.0, dir:  1, dur: 36000, op: 0.34 },
                { r: 0.95, span:  52, off:   0, w: 2.6, dir: -1, dur: 28000, op: 0.46 },
                { r: 0.95, span:  16, off:  90, w: 2.6, dir: -1, dur: 28000, op: 0.46 },
                { r: 0.95, span:  52, off: 180, w: 2.6, dir: -1, dur: 28000, op: 0.46 },
                { r: 0.95, span:  16, off: 270, w: 2.6, dir: -1, dur: 28000, op: 0.46 }
            ]

            Shape {
                id: ring
                anchors.fill: parent
                asynchronous: true
                preferredRendererType: Shape.CurveRenderer

                property var spec: modelData
                property real spin: 0
                NumberAnimation on spin {
                    running: true
                    loops: Animation.Infinite
                    from: 0; to: 360 * ring.spec.dir
                    duration: view.mode === "thinking" ? ring.spec.dur / 3.2
                                                       : ring.spec.dur
                }
                rotation: spin

                // rings arrive one after another as the HUD reveals
                opacity: ring.spec.op * Math.max(0, Math.min(1,
                             (view.revealed * 1.6) - index * 0.08))

                ShapePath {
                    strokeColor: view.accent
                    strokeWidth: ring.spec.w
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap

                    PathAngleArc {
                        centerX: rig.width / 2
                        centerY: rig.height / 2
                        radiusX: rig.width * ring.spec.r / 2
                        radiusY: rig.width * ring.spec.r / 2
                        startAngle: ring.spec.off
                        sweepAngle: ring.spec.span
                    }
                }
            }
        }

        // --- radial ticks ----------------------------------------------------
        Item {
            anchors.fill: parent
            rotation: view.orbitSpin * 0.12
            opacity: 0.34 * view.revealed
            Repeater {
                model: 60
                Rectangle {
                    readonly property real ang: index * (Math.PI * 2 / 60)
                    readonly property real rad: rig.width * 0.505
                    width: index % 5 === 0 ? view.s(9) : view.s(3)
                    height: 1.5
                    color: view.accent
                    antialiasing: true
                    x: rig.width / 2 + Math.cos(ang) * rad - width / 2
                    y: rig.height / 2 + Math.sin(ang) * rad - height / 2
                    rotation: ang * 180 / Math.PI
                }
            }
        }

        // --- the ball --------------------------------------------------------
        TarOrb {
            id: orb
            anchors.centerIn: parent
            width: rig.width * 0.54
            height: width
            coreSize: rig.width * 0.46
            mode: view.mode
            level: view.level
        }

        // --- text being eaten -------------------------------------------------
        Item {
            anchors.centerIn: parent
            width: rig.width; height: view.s(30)
            visible: view.swallowProgress > 0.001

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                width: rig.width * 0.92
                text: view.inFlight
                color: view.accent
                font.family: "JetBrains Mono"
                font.pixelSize: view.s(14)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                y: -rig.height * 0.52 * (1.0 - view.swallowProgress)
                scale: 1.0 - view.swallowProgress * 0.88
                opacity: 1.0 - view.swallowProgress * view.swallowProgress
            }
        }

        // --- the payload orbiting overhead ------------------------------------
        Item {
            id: orbitRig
            anchors.horizontalCenter: parent.horizontalCenter
            y: -rig.height * 0.14
            width: rig.width; height: rig.height * 0.5
            visible: view.phase === "orbit"

            Repeater {
                model: Math.min(26, Math.max(8, view.inFlight.length))
                Text {
                    readonly property int n: Math.min(26, Math.max(8, view.inFlight.length))
                    readonly property real ang:
                        (index / n) * Math.PI * 2 + (view.orbitSpin * Math.PI / 180)
                    readonly property real rx: rig.width * 0.40
                    readonly property real ry: rig.width * 0.115

                    text: view.inFlight.charAt(index % view.inFlight.length)
                    color: view.accent
                    font.family: "JetBrains Mono"
                    font.pixelSize: view.s(14)

                    x: orbitRig.width / 2 + Math.cos(ang) * rx - width / 2
                    y: orbitRig.height / 2 + Math.sin(ang) * ry - height / 2
                    opacity: 0.22 + 0.78 * ((Math.sin(ang) + 1) / 2)
                    scale: 0.7 + 0.4 * ((Math.sin(ang) + 1) / 2)
                }
            }
        }
    }

    // ================================================= context window (above)
    Item {
        id: contextWindow
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: rig.top
        anchors.bottomMargin: view.s(4)
        width: Math.min(parent.width * 0.80, view.s(600))
        height: ctxCol.implicitHeight + view.s(24)

        opacity: view.revealed
                 * ((view.lastUser !== "" || view.lastReply !== "") ? 1.0 : 0.0)
        y: (1.0 - view.revealed) * view.s(14)
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 280 } }

        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(view.theme.crust.r, view.theme.crust.g,
                           view.theme.crust.b, 0.66)
            border.width: 1
            border.color: Qt.rgba(view.accent.r, view.accent.g, view.accent.b, 0.30)
        }
        Repeater {
            model: 4
            Item {
                readonly property bool rightSide: index === 1 || index === 2
                readonly property bool bottomSide: index >= 2
                width: view.s(10); height: view.s(10)
                x: rightSide ? contextWindow.width - width : 0
                y: bottomSide ? contextWindow.height - height : 0
                Rectangle { width: parent.width; height: 1.5; color: view.accent
                            y: parent.bottomSide ? parent.height - height : 0 }
                Rectangle { width: 1.5; height: parent.height; color: view.accent
                            x: parent.rightSide ? parent.width - width : 0 }
            }
        }

        Column {
            id: ctxCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: view.s(16)
            anchors.rightMargin: view.s(16)
            spacing: view.s(6)

            // RowLayout, not Row: the body used to be given a hard-coded
            // width of (column - 48), which is wider than the space actually
            // left beside the label, so the reply overlapped its own "T.A.R."
            // tag. Let the layout do the arithmetic.
            Row {
                spacing: view.s(9)
                visible: view.lastUser !== ""
                Text {
                    width: view.s(44)
                    text: "YOU"
                    color: view.theme.overlay1
                    font.family: "JetBrains Mono"
                    font.pixelSize: view.s(9)
                    font.letterSpacing: view.s(1.5)
                }
                Text {
                    width: ctxCol.width - view.s(53)
                    text: view.lastUser
                    color: view.theme.subtext0
                    font.family: "JetBrains Mono"
                    font.pixelSize: view.s(11)
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                width: ctxCol.width; height: 1
                color: Qt.rgba(view.theme.surface1.r, view.theme.surface1.g,
                               view.theme.surface1.b, 0.4)
                visible: view.lastUser !== "" && view.lastReply !== ""
            }

            Row {
                spacing: view.s(9)
                visible: view.lastReply !== ""
                Text {
                    width: view.s(44)
                    text: "T.A.R."
                    color: view.accent
                    font.family: "JetBrains Mono"
                    font.pixelSize: view.s(9)
                    font.letterSpacing: view.s(1.5)
                    font.bold: true
                }
                Text {
                    width: ctxCol.width - view.s(53)
                    text: view.lastReply
                    color: view.theme.text
                    font.family: "JetBrains Mono"
                    font.pixelSize: view.s(12)
                    wrapMode: Text.Wrap
                    maximumLineCount: 5
                    elide: Text.ElideRight
                }
            }
        }
    }

    // ================================================ transient notice popup
    Item {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: rig.top
        anchors.bottomMargin: view.s(10)
        width: Math.min(parent.width * 0.7, view.s(520))
        height: toastBox.height
        opacity: 0.0
        visible: opacity > 0.01
        y: 0

        property color tint: view.toastKind === "error" ? view.theme.red
                           : view.toastKind === "act"   ? view.theme.green
                                                        : view.accent

        Rectangle {
            id: toastBox
            width: parent.width
            height: toastLabel.implicitHeight + view.s(18)
            color: Qt.rgba(view.theme.crust.r, view.theme.crust.g,
                           view.theme.crust.b, 0.92)
            border.width: 1
            border.color: Qt.rgba(toast.tint.r, toast.tint.g, toast.tint.b, 0.55)

            Rectangle {          // accent bar
                width: view.s(2); height: parent.height
                color: toast.tint
            }
            Text {
                id: toastLabel
                x: view.s(12)
                width: parent.width - view.s(24)
                anchors.verticalCenter: parent.verticalCenter
                text: view.toastText
                color: view.theme.text
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.family: "JetBrains Mono"
                font.pixelSize: view.s(11)
            }
        }

        // corner ticks, so it reads like the rest of the HUD
        Repeater {
            model: 4
            Item {
                readonly property bool rightSide: index === 1 || index === 2
                readonly property bool bottomSide: index >= 2
                width: view.s(8); height: view.s(8)
                x: rightSide ? toast.width - width : 0
                y: bottomSide ? toast.height - height : 0
                Rectangle { width: parent.width; height: 1.2; color: toast.tint
                            y: parent.bottomSide ? parent.height - height : 0 }
                Rectangle { width: 1.2; height: parent.height; color: toast.tint
                            x: parent.rightSide ? parent.width - width : 0 }
            }
        }

        SequentialAnimation {
            id: toastAnim
            ParallelAnimation {
                NumberAnimation { target: toast; property: "opacity"
                                  to: 1.0; duration: 180 }
                NumberAnimation { target: toast; property: "y"
                                  from: view.s(10); to: 0
                                  duration: 260; easing.type: Easing.OutCubic }
            }
            PauseAnimation { duration: 3400 }
            NumberAnimation { target: toast; property: "opacity"
                              to: 0.0; duration: 420 }
        }
    }

    function showToast(text, kind) {
        if (!text) return;
        view.toastText = text;
        view.toastKind = kind || "sys";
        toastAnim.restart();
    }

    // ======================================= controls, hugging the orb below
    Row {
        id: controlRow
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: rig.bottom
        anchors.topMargin: view.s(4)
        spacing: view.s(9)
        opacity: view.revealed
        y: (1.0 - view.revealed) * -view.s(14)
        visible: opacity > 0.01

        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: view.sttReady && view.micEnabled ? "󰍬" : "󰍭"
            label: !view.sttReady ? "NO MIC"
                 : view.micEnabled ? "MIC ON" : "MIC OFF"
            active: view.sttReady && view.micEnabled
            locked: !view.sttReady
            onClicked: view.micToggled(!view.micEnabled)
            onLockedClicked: view.setupRequested()
        }
        Item {
            id: audioPair
            width: pairRow.implicitWidth
            height: view.s(30)
            property bool hovered: pairHover.hovered || volHover.hovered
            HoverHandler { id: pairHover }

            Row {
                id: pairRow
                spacing: view.s(9)

        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: view.ttsReady && view.speakReplies ? "󰕾" : "󰖁"
            label: !view.ttsReady ? "NO VOICE"
                 : view.speakReplies ? "SPEAKS" : "SILENT"
            active: view.ttsReady && view.speakReplies
            locked: !view.ttsReady
            onClicked: view.speakToggled(!view.speakReplies)
            onLockedClicked: view.setupRequested()
        }
        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "󰗋"
            label: "READ"
            locked: !view.ttsReady
            enabled: view.lastReply !== ""
            opacity: view.lastReply !== "" ? (view.ttsReady ? 1.0 : 0.42) : 0.35
            onClicked: view.readAloudRequested(view.lastReply)
            onLockedClicked: view.setupRequested()
        }
            }

            Rectangle {
                y: -height - view.s(6)
                width: view.s(132); height: view.s(26)
                visible: audioPair.hovered
                color: Qt.rgba(view.theme.crust.r, view.theme.crust.g,
                               view.theme.crust.b, 0.95)
                border.width: 1
                border.color: Qt.rgba(view.accent.r, view.accent.g,
                                      view.accent.b, 0.5)
                HoverHandler { id: volHover }

                Row {
                    anchors.centerIn: parent
                    spacing: view.s(6)
                    Text {
                        text: "VOL"
                        color: view.theme.overlay1
                        font.family: "JetBrains Mono"
                        font.pixelSize: view.s(8)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Item {
                        width: view.s(80); height: view.s(14)
                        anchors.verticalCenter: parent.verticalCenter
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width; height: view.s(2)
                            color: Qt.rgba(view.theme.surface1.r,
                                           view.theme.surface1.g,
                                           view.theme.surface1.b, 0.8)
                        }
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width * view.volume; height: view.s(2)
                            color: view.accent
                        }
                        Rectangle {
                            x: parent.width * view.volume - width / 2
                            anchors.verticalCenter: parent.verticalCenter
                            width: view.s(4); height: view.s(12)
                            color: view.accent
                        }
                        MouseArea {
                            anchors.fill: parent
                            onPressed: (m) => view.volumeRequested(
                                Math.max(0, Math.min(1, m.x / width)))
                            onPositionChanged: (m) => {
                                if (pressed) view.volumeRequested(
                                    Math.max(0, Math.min(1, m.x / width)));
                            }
                        }
                    }
                    Text {
                        text: Math.round(view.volume * 100)
                        color: view.theme.subtext0
                        font.family: "JetBrains Mono"
                        font.pixelSize: view.s(8)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
        }

        // Orb mode had no way to reach setup or past chats at all -- you had to
        // switch to chat mode first. These make both reachable from here.
        TarHudButton {
            visible: view.busy
            theme: view.theme; accent: view.theme.red; scaleFn: view.hostScale
            glyph: "\u{f04db}"
            label: "STOP"
            onClicked: view.stopRequested()
        }
        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "\u{f0493}"
            label: "SETUP"
            onClicked: view.settingsRequested()
        }
    }

    // ============================================= top strip: navigation + exit
    // CHATS / CHAT / CLOSE belong with "where am I", not with the mic and the
    // volume. CLOSE sits top-right, where a window's close button belongs.
    Row {
        id: navRow
        anchors.top: parent.top
        anchors.right: parent.right
        // clear of the header block (title + model + status lines), which was
        // printing straight through these buttons
        anchors.topMargin: view.s(62)
        anchors.rightMargin: view.s(14)
        spacing: view.s(7)
        opacity: view.revealed
        visible: opacity > 0.01

        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "󰭹"
            label: "CHAT"
            onClicked: view.chatModeRequested()
        }
        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "\u{f054c}"
            label: "CHATS"
            onClicked: view.historyRequested()
        }
        TarHudButton {
            theme: view.theme; accent: view.theme.red; scaleFn: view.hostScale
            glyph: "\u{f0156}"
            label: "CLOSE"
            onClicked: view.closeRequested()
        }
    }

    // ==================================================== side rail: the console
    Column {
        id: sideRail
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: view.s(12)
        spacing: view.s(7)
        opacity: view.revealed
        visible: opacity > 0.01

        TarHudButton {
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "󰘳"
            label: "CONSOLE"
            active: view.consoleOpen
            onClicked: view.consoleRequested()
        }
        TarHudButton {
            visible: view.tasksActive
            theme: view.theme; accent: view.accent; scaleFn: view.hostScale
            glyph: "\u{f0954}"
            label: "TASKS"
            active: view.tasksOpen
            onClicked: view.tasksRequested()
        }
    }

    // ================================================== input, just below that
    Item {
        id: inputPill
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: controlRow.bottom
        anchors.topMargin: view.s(12)
        width: Math.min(parent.width * 0.62, view.s(500))
        height: view.s(42)
        opacity: view.revealed
        y: (1.0 - view.revealed) * -view.s(10)
        visible: opacity > 0.01

        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(view.theme.mantle.r, view.theme.mantle.g,
                           view.theme.mantle.b, 0.78)
            border.width: 1
            border.color: orbInput.activeFocus
                ? Qt.rgba(view.accent.r, view.accent.g, view.accent.b, 0.75)
                : Qt.rgba(view.theme.surface1.r, view.theme.surface1.g,
                          view.theme.surface1.b, 0.55)
            Behavior on border.color { ColorAnimation { duration: 150 } }
        }
        Repeater {
            model: 4
            Item {
                readonly property bool rightSide: index === 1 || index === 2
                readonly property bool bottomSide: index >= 2
                width: view.s(8); height: view.s(8)
                x: rightSide ? inputPill.width - width : 0
                y: bottomSide ? inputPill.height - height : 0
                Rectangle { width: parent.width; height: 1.5; color: view.accent
                            y: parent.bottomSide ? parent.height - height : 0 }
                Rectangle { width: 1.5; height: parent.height; color: view.accent
                            x: parent.rightSide ? parent.width - width : 0 }
            }
        }

        Row {
            anchors.fill: parent
            anchors.leftMargin: view.s(14)
            anchors.rightMargin: view.s(14)
            spacing: view.s(9)

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: ">"
                color: view.busy ? view.theme.peach : view.accent
                font.family: "JetBrains Mono"
                font.pixelSize: view.s(14)
                font.bold: true
            }
            TarVoiceStrip {
                visible: view.hearing
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - view.s(30) - voiceBtns.width
                height: view.s(30)
                theme: view.theme; accent: view.accent; scaleFn: view.hostScale
                levels: view.hearLevels
                busy: view.hearBusy
                label: view.hearLabel
            }
            TextInput {
                id: orbInput
                visible: !view.hearing
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - view.s(30) - voiceBtns.width
                onTextChanged: if (view.draft !== text) view.draftEdited(text)
                color: view.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: view.s(13)
                enabled: !view.busy
                clip: true

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: orbInput.text === ""
                    text: view.busy ? ""
                        : (view.micEnabled ? "speak, or type  /  for settings"
                                           : "ask_   ·   /  for settings")
                    color: view.theme.overlay0
                    font: orbInput.font
                }
                Keys.onReturnPressed: (event) => {
                    var t = text.trim();
                    if (t !== "" && !view.busy) {
                        if (t.charAt(0) !== "/") view.beginSwallow(t);
                        view.submitted(t);
                        text = "";
                    }
                    event.accepted = true;
                }
                Keys.onEscapePressed: (event) => {
                    if (view.hearing) { view.voiceCancel(); event.accepted = true; }
                    else event.accepted = false;
                }
            }
            Row {
                id: voiceBtns
                anchors.verticalCenter: parent.verticalCenter
                spacing: view.s(6)
                TarHudButton {
                    visible: !view.busy && !view.hearing
                    theme: view.theme; accent: view.accent; scaleFn: view.hostScale
                    glyph: "\u{f036c}"; label: "VOICE"
                    onClicked: view.voiceStart()
                }
                TarHudButton {
                    visible: view.hearing && !view.hearBusy
                    theme: view.theme; accent: view.theme.red; scaleFn: view.hostScale
                    glyph: "\u{f0156}"; label: "CANCEL"
                    onClicked: view.voiceCancel()
                }
                TarHudButton {
                    visible: view.hearing && !view.hearBusy
                    theme: view.theme; accent: view.theme.green; scaleFn: view.hostScale
                    glyph: "\u{f04db}"; label: "STOP"
                    onClicked: view.voiceStop()
                }
            }
        }
    }

    // ============================================== corner readouts (fade in)
    Column {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: view.s(16)
        spacing: view.s(3)
        opacity: view.revealed

        TarGlitch {
            text: "T.A.R."
            color: view.theme.text
            pixelSize: view.s(17)
            bold: true
            letterSpacing: view.s(3)
            intensity: view.mode === "thinking" ? 0.8
                     : view.mode === "error" ? 1.3 : 0.0
            burstInterval: view.mode === "thinking" ? 700 : 3400
            Behavior on intensity { NumberAnimation { duration: 260 } }
        }
        Row {
            spacing: view.s(6)
            Rectangle {
                width: view.s(5); height: view.s(5); radius: width / 2
                color: view.accent
                anchors.verticalCenter: parent.verticalCenter
                SequentialAnimation on opacity {
                    loops: Animation.Infinite; running: true
                    NumberAnimation { to: 0.25; duration: 780 }
                    NumberAnimation { to: 1.0; duration: 780 }
                }
            }
            Text {
                text: view.statusNote
                color: view.mode === "error" ? view.theme.red : view.theme.overlay1
                font.family: "JetBrains Mono"
                font.pixelSize: view.s(9)
            }
        }
    }

    Column {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: view.s(16)
        spacing: view.s(2)
        opacity: view.revealed
        visible: view.activeModel !== ""

        Text {
            anchors.right: parent.right
            text: "[ " + (view.activeTier || "?").toUpperCase() + " ]"
            color: view.accent
            font.family: "JetBrains Mono"
            font.pixelSize: view.s(10)
            font.letterSpacing: view.s(1)
        }
        Text {
            anchors.right: parent.right
            text: view.activeModel
            color: view.theme.subtext1
            font.family: "JetBrains Mono"
            font.pixelSize: view.s(9)
        }
        Text {
            anchors.right: parent.right
            // the safe/open lane is a LOCAL model setting; it means nothing
            // when the cloud brain is answering
            readonly property bool cloud: view.activeTier === "cloud"
            text: cloud ? (String(view.activeModel).indexOf("gemini") === 0
                           ? "// CLOUD · GEMINI FREE" : "// CLOUD · CLAUDE API")
                        : view.activeLane === "open" ? "// UNRESTRICTED" : "// STOCK"
            color: cloud ? view.theme.yellow
                         : view.activeLane === "open" ? view.theme.peach : view.theme.overlay0
            font.family: "JetBrains Mono"
            font.pixelSize: view.s(9)
            font.letterSpacing: view.s(1)
        }
    }

    function focusInput() { orbInput.forceActiveFocus(); }
}
