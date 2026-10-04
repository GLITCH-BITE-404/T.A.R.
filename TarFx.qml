import QtQuick

// T.A.R. effects layer -- one overlay on top of BOTH views (orb and chat), so
// an effect is visible whichever one is showing. Everything is plain
// rectangles/text/transforms (no shaders) and every animation stops when it
// ends: nothing here costs a frame while idle.
//
//   fx.play("glitch")          tear + jitter
//   fx.play("scan")            scanline sweep
//   fx.play("shock")           shockwave ring
//   fx.play("alert")           red flash + vignette
//   fx.play("flash")           white flash
//   fx.play("shake")           screen shake
//   fx.play("heartbeat")       double pulse
//   fx.play("matrix", "text")  green takeover + rain boost
//   fx.play("hack")            hex cascade + glitch
//   fx.play("party")           colour cycle
//   fx.play("barrelroll")      spins the whole panel
//   fx.play("selfdestruct")    3-2-1 countdown ... then nothing
//   fx.play("banner", "text")  big glitch text across the middle
Item {
    id: fx
    anchors.fill: parent
    enabled: false              // never eats clicks
    z: 1000

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    // read by the host: applied as a transform on the whole panel
    property real shakeX: 0
    property real shakeY: 0
    property real spin: 0
    // read by the host: multiplies the matrix rain in both views
    property real rainBoost: 1.0

    readonly property var effects: ["glitch", "scan", "shock", "alert", "flash",
        "shake", "heartbeat", "matrix", "hack", "party", "barrelroll",
        "selfdestruct", "banner"]

    function play(name, text) {
        switch (name) {
        case "glitch":      glitchAnim.restart(); shakeAnim.restart(); break;
        case "scan":        scanAnim.restart(); break;
        case "shock":       shockAnim.restart(); break;
        case "alert":       flash(fx.theme.red, 0.32); vignetteAnim.restart(); break;
        case "flash":       flash("white", 0.55); break;
        case "shake":       shakeAnim.restart(); break;
        case "heartbeat":   heartAnim.restart(); break;
        case "matrix":      matrixAnim.restart();
                            banner(text || "wake up.", fx.theme.green); break;
        case "hack":        hackAnim.restart(); glitchAnim.restart(); break;
        case "party":       partyAnim.restart(); shockAnim.restart(); break;
        case "barrelroll":  rollAnim.restart(); break;
        case "selfdestruct": destructAnim.restart(); break;
        case "banner":      banner(text || "T.A.R.", fx.accent); break;
        }
    }

    // ------------------------------------------------------------ flash
    Rectangle {
        id: flashRect
        anchors.fill: parent
        opacity: 0
        visible: opacity > 0.001
    }
    function flash(c, peak) {
        flashRect.color = c;
        flashAnim.peak = peak;
        flashAnim.restart();
    }
    SequentialAnimation {
        id: flashAnim
        property real peak: 0.5
        NumberAnimation { target: flashRect; property: "opacity"; to: flashAnim.peak; duration: 60 }
        NumberAnimation { target: flashRect; property: "opacity"; to: 0; duration: 520
                          easing.type: Easing.OutCubic }
    }

    // ------------------------------------------------------------ vignette (alert)
    Rectangle {
        id: vignette
        anchors.fill: parent
        color: "transparent"
        border.color: fx.theme.red
        border.width: fx.s(26)
        opacity: 0
        visible: opacity > 0.001
    }
    SequentialAnimation {
        id: vignetteAnim
        loops: 3
        NumberAnimation { target: vignette; property: "opacity"; to: 0.55; duration: 180 }
        NumberAnimation { target: vignette; property: "opacity"; to: 0.0; duration: 360 }
    }

    // ------------------------------------------------------------ scanline
    Rectangle {
        id: scanBar
        width: parent.width
        height: fx.s(70)
        y: -height
        visible: scanAnim.running
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5; color: Qt.rgba(fx.accent.r, fx.accent.g, fx.accent.b, 0.30) }
            GradientStop { position: 1.0; color: "transparent" }
        }
        Rectangle { width: parent.width; height: 1; anchors.centerIn: parent
                    color: fx.accent; opacity: 0.9 }
    }
    NumberAnimation {
        id: scanAnim
        target: scanBar; property: "y"
        from: -scanBar.height; to: fx.height
        duration: 900; easing.type: Easing.InOutSine
    }

    // ------------------------------------------------------------ shockwave
    Rectangle {
        id: ring
        width: fx.s(120); height: width; radius: width / 2
        anchors.centerIn: parent
        color: "transparent"
        border.color: fx.accent
        border.width: fx.s(3)
        opacity: 0; scale: 0.2
        visible: opacity > 0.001
    }
    ParallelAnimation {
        id: shockAnim
        NumberAnimation { target: ring; property: "scale"; from: 0.2; to: 9; duration: 750
                          easing.type: Easing.OutCubic }
        NumberAnimation { target: ring; property: "opacity"; from: 0.95; to: 0; duration: 750
                          easing.type: Easing.OutQuad }
    }
    SequentialAnimation {
        id: heartAnim
        ScriptAction { script: shockAnim.restart() }
        PauseAnimation { duration: 260 }
        ScriptAction { script: shockAnim.restart() }
    }

    // ------------------------------------------------------------ glitch tear
    // three horizontal bands that jump around and split colour for ~0.9s
    Repeater {
        id: tears
        model: 3
        Rectangle {
            property real jx: 0
            width: fx.width; height: fx.s(6 + index * 9)
            x: jx; y: 0
            color: index === 1 ? Qt.rgba(1, 0.2, 0.4, 0.28)
                               : Qt.rgba(fx.accent.r, fx.accent.g, fx.accent.b, 0.26)
            visible: glitchAnim.running
        }
    }
    Timer {
        id: glitchTick
        interval: 45; repeat: true
        running: glitchAnim.running
        onTriggered: {
            for (var i = 0; i < tears.count; i++) {
                var t = tears.itemAt(i);
                if (!t) continue;
                t.y = Math.random() * fx.height;
                t.jx = (Math.random() - 0.5) * fx.s(40);
            }
        }
    }
    PauseAnimation { id: glitchAnim; duration: 900 }

    // ------------------------------------------------------------ shake
    SequentialAnimation {
        id: shakeAnim
        loops: 6
        ParallelAnimation {
            NumberAnimation { target: fx; property: "shakeX"; to: fx.s(7); duration: 30 }
            NumberAnimation { target: fx; property: "shakeY"; to: -fx.s(3); duration: 30 }
        }
        ParallelAnimation {
            NumberAnimation { target: fx; property: "shakeX"; to: -fx.s(7); duration: 30 }
            NumberAnimation { target: fx; property: "shakeY"; to: fx.s(3); duration: 30 }
        }
        onFinished: { fx.shakeX = 0; fx.shakeY = 0; }
    }

    // ------------------------------------------------------------ barrel roll
    NumberAnimation {
        id: rollAnim
        target: fx; property: "spin"; from: 0; to: 360
        duration: 1100; easing.type: Easing.InOutCubic
        onFinished: fx.spin = 0
    }

    // ------------------------------------------------------------ matrix takeover
    Rectangle {
        id: greenTint
        anchors.fill: parent
        color: fx.theme.green
        opacity: 0
        visible: opacity > 0.001
    }
    SequentialAnimation {
        id: matrixAnim
        ParallelAnimation {
            NumberAnimation { target: greenTint; property: "opacity"; to: 0.12; duration: 300 }
            NumberAnimation { target: fx; property: "rainBoost"; to: 3.0; duration: 300 }
        }
        PauseAnimation { duration: 3200 }
        ParallelAnimation {
            NumberAnimation { target: greenTint; property: "opacity"; to: 0; duration: 900 }
            NumberAnimation { target: fx; property: "rainBoost"; to: 1.0; duration: 900 }
        }
    }

    // ------------------------------------------------------------ party
    Rectangle {
        id: partyTint
        anchors.fill: parent
        property real hue: 0
        color: Qt.hsla(hue, 0.9, 0.55, 1)
        opacity: 0
        visible: opacity > 0.001
    }
    SequentialAnimation {
        id: partyAnim
        NumberAnimation { target: partyTint; property: "opacity"; to: 0.16; duration: 200 }
        NumberAnimation { target: partyTint; property: "hue"; from: 0; to: 3; duration: 3600 }
        NumberAnimation { target: partyTint; property: "opacity"; to: 0; duration: 500 }
        onFinished: partyTint.hue = 0
    }

    // ------------------------------------------------------------ hex cascade
    Column {
        id: hexCol
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: fx.s(28)
        spacing: fx.s(2)
        opacity: 0
        visible: opacity > 0.001
        property int shown: 0
        Repeater {
            id: hexRows
            model: 16
            Text {
                visible: index < hexCol.shown
                text: ""
                color: fx.theme.green
                font.family: "JetBrains Mono"
                font.pixelSize: fx.s(11)
            }
        }
    }
    function hexLine() {
        var h = "0123456789abcdef", out = "0x";
        for (var i = 0; i < 8; i++) out += h[Math.floor(Math.random() * 16)];
        var verbs = ["inject", "bypass", "decrypt", "spoof", "escalate", "exfil", "patch"];
        return out + "  " + verbs[Math.floor(Math.random() * verbs.length)]
             + " ...... " + (Math.random() < 0.85 ? "[ OK ]" : "[ RETRY ]");
    }
    Timer {
        id: hexTick
        interval: 70; repeat: true
        onTriggered: {
            if (hexCol.shown >= hexRows.count) { stop(); return; }
            var t = hexRows.itemAt(hexCol.shown);
            if (t) t.text = fx.hexLine();
            hexCol.shown++;
        }
    }
    SequentialAnimation {
        id: hackAnim
        ScriptAction { script: { hexCol.shown = 0; hexCol.opacity = 1; hexTick.restart(); } }
        PauseAnimation { duration: 1500 }
        ScriptAction { script: fx.banner("ACCESS GRANTED", fx.theme.green) }
        PauseAnimation { duration: 1200 }
        NumberAnimation { target: hexCol; property: "opacity"; to: 0; duration: 500 }
    }

    // ------------------------------------------------------------ banner text
    TarGlitch {
        id: bannerText
        anchors.centerIn: parent
        text: ""
        color: fx.accent
        pixelSize: fx.s(40)
        bold: true
        letterSpacing: fx.s(4)
        intensity: 1.0
        opacity: 0
        visible: opacity > 0.001
    }
    function banner(t, c) {
        bannerText.text = t;
        bannerText.color = c || fx.accent;
        bannerAnim.restart();
    }
    SequentialAnimation {
        id: bannerAnim
        NumberAnimation { target: bannerText; property: "opacity"; to: 1; duration: 120 }
        PauseAnimation { duration: 1500 }
        NumberAnimation { target: bannerText; property: "opacity"; to: 0; duration: 500 }
    }

    // ------------------------------------------------------------ self destruct
    SequentialAnimation {
        id: destructAnim
        ScriptAction { script: { fx.banner("SELF-DESTRUCT IN 3", fx.theme.red);
                                 fx.flash(fx.theme.red, 0.25); vignetteAnim.restart(); } }
        PauseAnimation { duration: 1000 }
        ScriptAction { script: { fx.banner("2", fx.theme.red); fx.flash(fx.theme.red, 0.3);
                                 shakeAnim.restart(); } }
        PauseAnimation { duration: 1000 }
        ScriptAction { script: { fx.banner("1", fx.theme.red); fx.flash(fx.theme.red, 0.4);
                                 shakeAnim.restart(); glitchAnim.restart(); } }
        PauseAnimation { duration: 1100 }
        ScriptAction { script: { fx.flash("white", 0.8); shockAnim.restart(); } }
        PauseAnimation { duration: 500 }
        ScriptAction { script: fx.banner("...nah.", fx.accent) }
    }
}
