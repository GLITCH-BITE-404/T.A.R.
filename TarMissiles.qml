import QtQuick
import Quickshell
import Quickshell.Io
import "."

// EVIL MODE easter egg -- MISSILE COMMAND. Pure theatre: a dot-matrix world
// map (Natural Earth land, public domain), a fake "hacking" log, a 20 s
// launch countdown, a warhead arcing to the target, impact rings.
// Nothing real happens; a small // SIMULATION tag says so.
Item {
    id: mc
    signal closed()
    property var theme
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property bool open: false
    // target (lat/lon from OpenStreetMap via tar_tools)
    property string target: ""
    property real tlat: 0
    property real tlon: 0
    // launch site: SILO-7 (Nevada desert) unless the target is close to it
    readonly property real slat: (Math.abs(tlat - 37.2) < 25 && Math.abs(tlon + 115.8) < 40) ? 64.8 : 37.2
    readonly property real slon: (Math.abs(tlat - 37.2) < 25 && Math.abs(tlon + 115.8) < 40) ? -147.7 : -115.8

    property string phase: "idle"       // idle | hack | flight | impact
    property real flight: 0             // 0..1 along the arc
    property int countdown: 20
    property var log: []
    property int lineIdx: 0
    readonly property color red: "#ff2a3d"
    readonly property color dim: "#7a1a24"

    function strike(name, lat, lon) {
        mc.target = name; mc.tlat = lat; mc.tlon = lon;
        mc.log = []; mc.lineIdx = 0; mc.flight = 0; mc.countdown = 20;
        mc.phase = "hack";
        hackTimer.restart();
        overlay.requestPaint();
    }
    function standby() {
        mc.target = ""; mc.phase = "idle"; mc.log = ["> MISSILE COMMAND ONLINE", "> awaiting target...",
            "> say: \"send missiles to <city>\""];
        overlay.requestPaint();
    }
    function fmtLat(v) { return Math.abs(v).toFixed(2) + (v >= 0 ? "N" : "S"); }
    function fmtLon(v) { return Math.abs(v).toFixed(2) + (v >= 0 ? "E" : "W"); }
    function hex(n) { var h = ""; for (var i = 0; i < n; i++) h += "0123456789ABCDEF"[Math.floor(Math.random() * 16)]; return h; }

    readonly property var script: [
        "> HACKING THE MAINFRAME ...",
        "> bypassing NORAD firewall  [####------]",
        "> bypassing NORAD firewall  [##########] 100%",
        "> injecting payload 0x" + hex(8) + " ... ok",
        "> connecting to the nukes model XR-77 \"HELLFIRE\" ...",
        "> model XR-77 running  (core temp 9001K)",
        "> connecting to the IR signals ...",
        "> converting IR -> radio wave @ 4.2 GHz",
        "> uplink to SILO-7 established",
        "> TARGET LOCKED: %T  %C",
        "> setting up the launch ...",
        "> arming warhead ... ARMED",
        "> NUKES LAUNCHED",
        "> predicted fall in 20 seconds"]

    Timer {
        id: hackTimer
        interval: 430; repeat: true
        onTriggered: {
            if (mc.lineIdx >= mc.script.length) {
                stop();
                mc.phase = "flight";
                flightAnim.restart();
                cdTimer.restart();
                return;
            }
            var l = mc.script[mc.lineIdx].replace("%T", mc.target.toUpperCase())
                       .replace("%C", mc.fmtLat(mc.tlat) + " " + mc.fmtLon(mc.tlon));
            var a = mc.log.slice(); a.push(l);
            mc.log = a.slice(-16);
            mc.lineIdx++;
        }
    }
    NumberAnimation {
        id: flightAnim
        target: mc; property: "flight"; from: 0; to: 1; duration: 20000
        easing.type: Easing.InOutSine
        onFinished: { mc.phase = "impact"; impactAnim.restart(); var a = mc.log.slice();
                      a.push("> IMPACT CONFIRMED: " + mc.target.toUpperCase()); mc.log = a.slice(-16); }
    }
    Timer {
        id: cdTimer; interval: 1000; repeat: true
        onTriggered: { if (mc.countdown > 0) mc.countdown--; else stop(); }
    }
    property real boom: 0
    NumberAnimation { id: impactAnim; target: mc; property: "boom"; from: 0; to: 1; duration: 2600
                      easing.type: Easing.OutCubic }
    onFlightChanged: overlay.requestPaint()
    onBoomChanged: overlay.requestPaint()

    // ---- land dots
    property var dots: []
    FileView {
        path: Quickshell.env("HOME") + "/.config/hypr/scripts/quickshell/tar/assets/landdots.json"
        onLoaded: { try { mc.dots = JSON.parse(text()).dots; base.requestPaint(); } catch (e) {} }
    }

    visible: opacity > 0.01
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 240 } }
    MouseArea { anchors.fill: parent; hoverEnabled: true }
    onOpenChanged: if (!open) { hackTimer.stop(); flightAnim.stop(); cdTimer.stop(); }

    Rectangle { anchors.fill: parent; color: "#070103" }
    Rectangle { anchors.fill: parent; color: "transparent"; border.width: 1; border.color: mc.red }

    // ---- header
    Item {
        id: head
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: mc.s(14)
        height: mc.s(36)
        TarGlitch {
            anchors.verticalCenter: parent.verticalCenter
            text: "◢ MISSILE COMMAND"
            color: mc.red; pixelSize: mc.s(18); bold: true; letterSpacing: mc.s(3)
            intensity: mc.phase === "flight" || mc.phase === "hack" ? 0.9 : 0.25
            burstInterval: 900
        }
        Text {
            anchors.right: closeBtn.left; anchors.rightMargin: mc.s(12)
            anchors.verticalCenter: parent.verticalCenter
            text: mc.phase === "flight" ? "T-" + (mc.countdown < 10 ? "0" : "") + mc.countdown
                : mc.phase === "impact" ? "IMPACT" : mc.phase === "hack" ? "BREACHING" : "STANDBY"
            color: mc.red
            font.family: "JetBrains Mono"; font.pixelSize: mc.s(22); font.bold: true
        }
        TarHudButton {
            id: closeBtn
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            theme: mc.theme; accent: mc.red; scaleFn: mc.scaleFn
            glyph: "\u{f0156}"; label: "CLOSE"
            onClicked: mc.closed()
        }
    }

    // ---- map
    Rectangle {
        id: mapBox
        anchors.left: parent.left; anchors.top: head.bottom; anchors.bottom: parent.bottom
        anchors.margins: mc.s(14)
        width: parent.width * 0.66
        color: "#0b0204"
        border.width: 1; border.color: mc.dim
        clip: true

        // equirectangular: lon -180..180, lat 84..-58 (what the dots cover),
        // kept at its true 360:142 shape and centred (it was stretched tall)
        readonly property real mw: Math.min(width, height * 360 / 142)
        readonly property real mh: mw * 142 / 360
        readonly property real ox: (width - mw) / 2
        readonly property real oy: (height - mh) / 2
        function px(lon) { return ox + (lon + 180) / 360 * mw; }
        function py(lat) { return oy + (84 - lat) / 142 * mh; }

        Canvas {
            id: base
            anchors.fill: parent
            renderStrategy: Canvas.Cooperative
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var c = getContext("2d");
                c.reset();
                c.strokeStyle = "rgba(255,42,61,0.10)";
                c.lineWidth = 1;
                for (var lo = -150; lo <= 150; lo += 30) { c.beginPath(); c.moveTo(mapBox.px(lo), mapBox.oy); c.lineTo(mapBox.px(lo), mapBox.oy + mapBox.mh); c.stroke(); }
                for (var la = -30; la <= 60; la += 30) { c.beginPath(); c.moveTo(mapBox.ox, mapBox.py(la)); c.lineTo(mapBox.ox + mapBox.mw, mapBox.py(la)); c.stroke(); }
                var r = Math.max(1.5, mapBox.mw / 180 * 0.8);
                c.fillStyle = "rgba(255,60,80,0.55)";
                for (var i = 0; i < mc.dots.length; i++) {
                    var d = mc.dots[i];
                    c.fillRect(mapBox.px(d[0]) - r / 2, mapBox.py(d[1]) - r / 2, r, r);
                }
            }
        }
        // arc, warhead, impact
        Canvas {
            id: overlay
            anchors.fill: parent
            onPaint: {
                var c = getContext("2d");
                c.reset();
                if (mc.target === "") return;
                var x0 = mapBox.px(mc.slon), y0 = mapBox.py(mc.slat);
                var x1 = mapBox.px(mc.tlon), y1 = mapBox.py(mc.tlat);
                var cx = (x0 + x1) / 2, cy = Math.min(y0, y1) - Math.hypot(x1 - x0, y1 - y0) * 0.35;
                function pt(t) { var u = 1 - t; return [u*u*x0 + 2*u*t*cx + t*t*x1, u*u*y0 + 2*u*t*cy + t*t*y1]; }
                // planned path (dashed)
                c.setLineDash([4, 6]);
                c.strokeStyle = "rgba(255,42,61,0.35)"; c.lineWidth = 1;
                c.beginPath(); c.moveTo(x0, y0); c.quadraticCurveTo(cx, cy, x1, y1); c.stroke();
                c.setLineDash([]);
                // silo
                c.fillStyle = "#ff2a3d"; c.fillRect(x0 - 3, y0 - 3, 6, 6);
                c.font = "bold 10px monospace"; c.fillText("SILO-7", x0 + 7, y0 + 3);
                // trail + warhead
                if (mc.phase === "flight" || mc.phase === "impact") {
                    var t = mc.phase === "impact" ? 1 : mc.flight;
                    c.strokeStyle = "rgba(255,190,90,0.9)"; c.lineWidth = 2;
                    c.beginPath(); c.moveTo(x0, y0);
                    for (var k = 1; k <= 40; k++) { var p = pt(t * k / 40); c.lineTo(p[0], p[1]); }
                    c.stroke();
                    if (mc.phase === "flight") {
                        var h = pt(t);
                        var g = c.createRadialGradient(h[0], h[1], 0, h[0], h[1], 12);
                        g.addColorStop(0, "rgba(255,240,200,1)"); g.addColorStop(1, "rgba(255,80,40,0)");
                        c.fillStyle = g; c.beginPath(); c.arc(h[0], h[1], 12, 0, Math.PI * 2); c.fill();
                    }
                }
                // impact rings + flash
                if (mc.phase === "impact") {
                    for (var ring = 0; ring < 3; ring++) {
                        var b = Math.max(0, mc.boom - ring * 0.15);
                        c.strokeStyle = "rgba(255,200,120," + (1 - b) + ")"; c.lineWidth = 3 - ring;
                        c.beginPath(); c.arc(x1, y1, 6 + b * 90, 0, Math.PI * 2); c.stroke();
                    }
                    var f = c.createRadialGradient(x1, y1, 0, x1, y1, 40 + mc.boom * 40);
                    f.addColorStop(0, "rgba(255,255,230," + (1 - mc.boom) + ")");
                    f.addColorStop(1, "rgba(255,60,20,0)");
                    c.fillStyle = f; c.beginPath(); c.arc(x1, y1, 40 + mc.boom * 40, 0, Math.PI * 2); c.fill();
                }
            }
        }
        // target reticle
        Item {
            visible: mc.target !== ""
            x: mapBox.px(mc.tlon) - width / 2
            y: mapBox.py(mc.tlat) - height / 2
            width: mc.s(44); height: width
            Rectangle {
                anchors.centerIn: parent
                width: parent.width; height: width; radius: width / 2
                color: "transparent"; border.width: 2; border.color: mc.red
                SequentialAnimation on scale {
                    loops: Animation.Infinite; running: mc.open && mc.phase !== "impact"
                    NumberAnimation { to: 0.55; duration: 600 }
                    NumberAnimation { to: 1.0; duration: 600 }
                }
            }
            Rectangle { anchors.centerIn: parent; width: parent.width * 1.6; height: 1; color: mc.red }
            Rectangle { anchors.centerIn: parent; width: 1; height: parent.height * 1.6; color: mc.red }
            Text {
                x: parent.width + mc.s(4); y: -mc.s(14)
                text: mc.target.toUpperCase() + "\n" + mc.fmtLat(mc.tlat) + " " + mc.fmtLon(mc.tlon)
                color: mc.red
                font.family: "JetBrains Mono"; font.pixelSize: mc.s(10); font.bold: true
            }
        }
        // impact banner
        Text {
            anchors.centerIn: parent
            visible: mc.phase === "impact" && mc.boom > 0.35
            text: "IMPACT CONFIRMED"
            color: mc.red
            font.family: "JetBrains Mono"; font.pixelSize: mc.s(28); font.bold: true
            font.letterSpacing: mc.s(4)
            opacity: Math.min(1, (mc.boom - 0.35) * 3)
        }
        // honest tag
        Text {
            anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: mc.s(6)
            text: "// SIMULATION"
            color: mc.dim
            font.family: "JetBrains Mono"; font.pixelSize: mc.s(8)
        }
    }

    // ---- terminal
    Rectangle {
        anchors.left: mapBox.right; anchors.right: parent.right
        anchors.top: head.bottom; anchors.bottom: parent.bottom
        anchors.margins: mc.s(14)
        color: "#0b0204"
        border.width: 1; border.color: mc.dim
        clip: true
        Column {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: mc.s(10)
            spacing: mc.s(5)
            Repeater {
                model: mc.log
                Text {
                    required property var modelData
                    width: parent.width
                    text: modelData
                    wrapMode: Text.WrapAnywhere
                    color: /LAUNCHED|LOCKED|IMPACT|fall in/.test(modelData) ? mc.red : "#e6c9cd"
                    font.family: "JetBrains Mono"; font.pixelSize: mc.s(10)
                    font.bold: /LAUNCHED|IMPACT/.test(modelData)
                }
            }
            Text {
                visible: mc.phase === "hack"
                text: "_"
                color: mc.red
                font.family: "JetBrains Mono"; font.pixelSize: mc.s(10)
                SequentialAnimation on opacity {
                    loops: Animation.Infinite; running: mc.open
                    NumberAnimation { to: 0; duration: 300 }
                    NumberAnimation { to: 1; duration: 300 }
                }
            }
        }
    }
}
