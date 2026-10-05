import QtQuick
import Quickshell
import Quickshell.Io
import "."

// EVIL MODE easter egg -- MISSILE COMMAND. Pure theatre, narrated:
//   ROUTE   trace route bouncing through proxy servers around the world
//   BREACH  "breaking" the target country's defense grid
//   LOCK    reticle locks on a real city (a named country -> one of its cities)
//   FLIGHT  20 s great-circle flight with telemetry, map zooms in at the end
//   IMPACT  flash, shockwave, then the tab closes itself
// Map: Natural Earth (public domain). Nothing real happens; tagged // SIMULATION.
Item {
    id: mc
    signal closed()
    signal narrate(string text)
    signal shake()
    property var theme
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property bool open: false
    property string target: ""          // "Tehran, Iran"
    property string city: ""
    property string country: ""
    property real tlat: 0
    property real tlon: 0
    property var route: []              // [[name, lat, lon, ip], ...]
    property real olat: 32.08           // T.A.R. CORE (origin of the trace)
    property real olon: 34.78
    property var silo: ["SILO-7 // NEVADA", 37.2, -115.8]
    readonly property var silos: [["SILO-7 // NEVADA", 37.2, -115.8], ["SILO-3 // YAKUTSK", 62.0, 129.7],
                                  ["SILO-9 // KERGUELEN", -49.3, 69.2], ["SILO-4 // SVALBARD", 78.2, 15.6],
                                  ["SILO-2 // ATACAMA", -24.5, -69.2]]

    property string phase: "idle"       // idle | route | breach | lock | flight | impact
    property real t: 0
    property real flight: 0
    property int countdown: 20
    property real zoom: 1
    property real boom: 0
    property real hopShown: 0
    property var log: []
    readonly property color red: "#ff2a3d"
    readonly property color dim: "#5c141c"
    readonly property color amber: "#ffb347"

    // ---------------------------------------------------------------- geo
    function rad(d) { return d * Math.PI / 180; }
    function deg(r) { return r * 180 / Math.PI; }
    function vec(lat, lon) { var a = rad(lat), b = rad(lon); return [Math.cos(a) * Math.cos(b), Math.cos(a) * Math.sin(b), Math.sin(a)]; }
    function gcDist(la1, lo1, la2, lo2) {
        var a = vec(la1, lo1), b = vec(la2, lo2);
        return Math.acos(Math.max(-1, Math.min(1, a[0]*b[0] + a[1]*b[1] + a[2]*b[2])));
    }
    function gcPoint(la1, lo1, la2, lo2, f) {     // slerp along the great circle
        var a = vec(la1, lo1), b = vec(la2, lo2);
        var w = gcDist(la1, lo1, la2, lo2);
        if (w < 1e-6) return [la1, lo1];
        var s1 = Math.sin((1 - f) * w) / Math.sin(w), s2 = Math.sin(f * w) / Math.sin(w);
        var x = s1*a[0] + s2*b[0], y = s1*a[1] + s2*b[1], z = s1*a[2] + s2*b[2];
        return [deg(Math.atan2(z, Math.sqrt(x*x + y*y))), deg(Math.atan2(y, x))];
    }
    function fmtLat(v) { return Math.abs(v).toFixed(2) + "°" + (v >= 0 ? "N" : "S"); }
    function fmtLon(v) { return Math.abs(v).toFixed(2) + "°" + (v >= 0 ? "E" : "W"); }
    function hex(n) { var h = ""; for (var i = 0; i < n; i++) h += "0123456789ABCDEF"[Math.floor(Math.random() * 16)]; return h; }
    function addLog(l) { var a = mc.log.slice(); a.push(l); mc.log = a.slice(-18); }

    // ---------------------------------------------------------------- run
    function strike(label, cityName, countryName, lat, lon, routeJson, oLat, oLon) {
        mc.target = label; mc.city = cityName || label; mc.country = countryName || "";
        mc.tlat = lat; mc.tlon = lon;
        try { mc.route = JSON.parse(routeJson || "[]"); } catch (e) { mc.route = []; }
        if (!isNaN(oLat) && !isNaN(oLon)) { mc.olat = oLat; mc.olon = oLon; }
        // silo: farthest one still under ~13,000 km (a believable ICBM shot)
        var best = mc.silos[0], bd = -1;
        for (var i = 0; i < mc.silos.length; i++) {
            var d = mc.gcDist(mc.silos[i][1], mc.silos[i][2], lat, lon) * 6371;
            if (d > 2500 && d < 13000 && d > bd) { bd = d; best = mc.silos[i]; }
        }
        mc.silo = best;
        mc.log = []; mc.flight = 0; mc.zoom = 1; mc.boom = 0; mc.countdown = 20; mc.hopShown = 0;
        mc.phase = "route"; mc.t = 0;
        mc.addLog("> ESTABLISHING COVERT UPLINK ...");
        seq.restart();
        overlay.requestPaint();
    }
    function standby() {
        seq.stop();
        mc.target = ""; mc.route = []; mc.phase = "idle"; mc.zoom = 1;
        mc.log = ["> STRATEGIC COMMAND ONLINE", "> all silos green", "> awaiting target ...",
                  "> say: \"send missiles to <city or country>\""];
        overlay.requestPaint();
    }

    SequentialAnimation {
        id: seq
        // ROUTE: one proxy hop every 1.3 s (narration waits for T.A.R.'s spoken reply)
        ParallelAnimation {
            NumberAnimation { target: mc; property: "hopShown"; from: 0; to: 4.01; duration: 5200 }
            SequentialAnimation {
                PauseAnimation { duration: 1800 }
                ScriptAction { script: mc.narrate("Accessing the mainframe. Routing through "
                    + mc.route.length + " proxies. They will never trace me.") }
            }
        }
        PauseAnimation { duration: 400 }
        // BREACH
        ScriptAction { script: { mc.phase = "breach"; mc.t = 0;
            mc.narrate("Breaching the " + (mc.country || mc.city) + " defense grid.");
            mc.addLog("> scanning " + (mc.country || mc.city).toUpperCase() + " DEFENSE GRID ...");
            mc.addLog("> exploit CVE-2026-" + Math.floor(1000 + Math.random() * 8999) + " -> payload 0x" + mc.hex(8)); } }
        NumberAnimation { target: mc; property: "t"; from: 0; to: 1; duration: 3600 }
        ScriptAction { script: { mc.addLog("> firewall: DOWN   radar: BLIND");
            mc.addLog("> connecting to the nukes model XR-77 \"HELLFIRE\" ... running");
            mc.addLog("> IR signals -> radio wave @ 4.2 GHz ... locked"); } }
        // LOCK
        ScriptAction { script: { mc.phase = "lock"; mc.t = 0;
            mc.narrate("Target locked. " + mc.city + ".");
            mc.addLog("> TARGET LOCKED: " + mc.city.toUpperCase() + "  " + mc.fmtLat(mc.tlat) + " " + mc.fmtLon(mc.tlon));
            mc.addLog("> " + mc.silo[0] + " armed"); } }
        NumberAnimation { target: mc; property: "t"; from: 0; to: 1; duration: 2600 }
        // FLIGHT
        ScriptAction { script: { mc.phase = "flight"; mc.countdown = 20; cdTimer.restart();
            mc.narrate("Missiles launched. Impact in twenty seconds.");
            mc.addLog("> NUKES LAUNCHED from " + mc.silo[0]);
            mc.addLog("> predicted fall in 20 seconds"); } }
        ParallelAnimation {
            NumberAnimation { target: mc; property: "flight"; from: 0; to: 1; duration: 20000; easing.type: Easing.InOutSine }
            SequentialAnimation {
                PauseAnimation { duration: 15500 }
                NumberAnimation { target: mc; property: "zoom"; from: 1; to: 2.6; duration: 4500; easing.type: Easing.InQuad }
            }
        }
        // IMPACT
        ScriptAction { script: { mc.phase = "impact"; mc.shake();
            mc.narrate("Impact confirmed. Evil wins again.");
            mc.addLog("> IMPACT CONFIRMED: " + mc.city.toUpperCase()); } }
        NumberAnimation { target: mc; property: "boom"; from: 0; to: 1; duration: 3000; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 3500 }
        ScriptAction { script: mc.closed() }            // never stuck on this screen
    }
    property int hopLogged: 0
    onHopShownChanged: {
        var n = Math.min(Math.floor(hopShown), route.length);
        while (hopLogged < n) {
            var h = route[hopLogged];
            addLog("> hop " + (hopLogged + 1) + "  " + h[3] + "  (" + h[0] + ")  " + (40 + Math.floor(Math.random() * 180)) + "ms");
            hopLogged++;
        }
        if (hopShown === 0) hopLogged = 0;
        overlay.requestPaint();
    }
    Timer {
        id: cdTimer; interval: 1000; repeat: true
        onTriggered: {
            if (mc.countdown > 0) mc.countdown--;
            if (mc.countdown === 5) mc.narrate("Say goodbye.");
            if (mc.countdown <= 0) stop();
        }
    }
    onFlightChanged: overlay.requestPaint()
    onBoomChanged: overlay.requestPaint()
    onTChanged: if (phase === "breach" || phase === "lock") overlay.requestPaint()
    onOpenChanged: { if (!open) { seq.stop(); cdTimer.stop(); } else forceActiveFocus(); }

    // telemetry (fake but physically shaped)
    readonly property real distKm: gcDist(silo[1], silo[2], tlat, tlon) * 6371
    readonly property real altKm: phase === "flight" ? 1200 * Math.sin(Math.PI * flight) * Math.min(1, distKm / 9000) : 0
    readonly property real mach: phase === "flight" ? 4 + 19 * Math.sin(Math.PI * Math.min(1, flight * 1.15)) : 0

    // ---- land dots
    property var dots: []
    FileView {
        path: Quickshell.env("HOME") + "/.config/hypr/scripts/quickshell/tar/assets/landdots.json"
        onLoaded: { try { mc.dots = JSON.parse(text()).dots; base.requestPaint(); } catch (e) {} }
    }

    visible: opacity > 0.01
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 260 } }
    MouseArea { anchors.fill: parent; hoverEnabled: true }
    Keys.onEscapePressed: mc.closed()

    Rectangle { anchors.fill: parent; color: "#050102" }
    Rectangle { anchors.fill: parent; color: "transparent"; border.width: 1; border.color: mc.dim }

    // ---- header
    Item {
        id: head
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: mc.s(14)
        height: mc.s(38)
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: mc.s(2)
            TarGlitch {
                text: "◢ STRATEGIC COMMAND // EVIL.T.A.R"
                color: mc.red; pixelSize: mc.s(15); bold: true; letterSpacing: mc.s(2.5)
                intensity: mc.phase === "breach" || mc.phase === "impact" ? 1.0 : 0.15
                burstInterval: 1200
            }
            Text {
                text: mc.phase === "idle" ? "DEFCON 3  ·  ALL SILOS GREEN"
                    : "DEFCON 1  ·  " + (mc.phase === "route" ? "TRACE ROUTE" : mc.phase === "breach" ? "BREACHING"
                       : mc.phase === "lock" ? "TARGET ACQUISITION" : mc.phase === "flight" ? "WARHEAD IN FLIGHT" : "IMPACT")
                color: mc.phase === "idle" ? "#9a6b70" : mc.amber
                font.family: "JetBrains Mono"; font.pixelSize: mc.s(9); font.letterSpacing: mc.s(1.5)
            }
        }
        Text {
            anchors.right: closeBtn.left; anchors.rightMargin: mc.s(14)
            anchors.verticalCenter: parent.verticalCenter
            visible: mc.phase === "flight" || mc.phase === "impact"
            text: mc.phase === "impact" ? "T+00" : "T-" + (mc.countdown < 10 ? "0" : "") + mc.countdown
            color: mc.red
            font.family: "JetBrains Mono"; font.pixelSize: mc.s(24); font.bold: true
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
        width: parent.width * 0.68
        color: "#080203"
        border.width: 1; border.color: mc.dim
        clip: true

        readonly property real mw: Math.min(width, height * 360 / 142)
        readonly property real mh: mw * 142 / 360
        readonly property real ox: (width - mw) / 2
        readonly property real oy: (height - mh) / 2
        function px(lon) { return ox + (lon + 180) / 360 * mw; }
        function py(lat) { return oy + (84 - lat) / 142 * mh; }

        Item {
            id: world
            anchors.fill: parent
            transform: Scale {
                origin.x: mapBox.px(mc.tlon); origin.y: mapBox.py(mc.tlat)
                xScale: mc.zoom; yScale: mc.zoom
            }
            Canvas {
                id: base
                anchors.fill: parent
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    var c = getContext("2d");
                    c.reset();
                    c.strokeStyle = "rgba(255,42,61,0.07)"; c.lineWidth = 1;
                    for (var lo = -150; lo <= 150; lo += 30) { c.beginPath(); c.moveTo(mapBox.px(lo), mapBox.oy); c.lineTo(mapBox.px(lo), mapBox.oy + mapBox.mh); c.stroke(); }
                    for (var la = -30; la <= 60; la += 30) { c.beginPath(); c.moveTo(mapBox.ox, mapBox.py(la)); c.lineTo(mapBox.ox + mapBox.mw, mapBox.py(la)); c.stroke(); }
                    c.strokeStyle = "rgba(255,42,61,0.16)";
                    c.beginPath(); c.moveTo(mapBox.ox, mapBox.py(0)); c.lineTo(mapBox.ox + mapBox.mw, mapBox.py(0)); c.stroke();
                    var r = Math.max(1.2, mapBox.mw / 180 * 0.62);
                    c.fillStyle = "rgba(255,52,72,0.42)";
                    for (var i = 0; i < mc.dots.length; i++) {
                        var d = mc.dots[i];
                        c.fillRect(mapBox.px(d[0]) - r / 2, mapBox.py(d[1]) - r / 2, r, r);
                    }
                    c.fillStyle = "rgba(255,120,130,0.45)";
                    for (var k = 0; k < mc.silos.length; k++)
                        c.fillRect(mapBox.px(mc.silos[k][2]) - 2, mapBox.py(mc.silos[k][1]) - 2, 4, 4);
                }
            }
            Canvas {
                id: overlay
                anchors.fill: parent
                onPaint: {
                    var c = getContext("2d");
                    c.reset();
                    if (mc.target === "") return;
                    function P(lat, lon) { return [mapBox.px(lon), mapBox.py(lat)]; }
                    function gcPath(la1, lo1, la2, lo2, upto, n) {
                        c.beginPath();
                        var prev = null;
                        for (var k = 0; k <= n; k++) {
                            var g = mc.gcPoint(la1, lo1, la2, lo2, upto * k / n), p = P(g[0], g[1]);
                            if (prev === null || Math.abs(p[0] - prev[0]) > mapBox.mw / 2) c.moveTo(p[0], p[1]);
                            else c.lineTo(p[0], p[1]);
                            prev = p;
                        }
                        c.stroke();
                        return prev;
                    }
                    c.font = "9px monospace";
                    // trace route: T.A.R. core -> proxies -> target country
                    var hops = Math.min(Math.floor(mc.hopShown), mc.route.length);
                    var pts = [[mc.olat, mc.olon]];
                    for (var h = 0; h < hops; h++) pts.push([mc.route[h][1], mc.route[h][2]]);
                    if (mc.phase !== "route") pts.push([mc.tlat, mc.tlon]);
                    c.strokeStyle = "rgba(90,220,255,0.55)"; c.lineWidth = 1.2;
                    c.setLineDash([3, 4]);
                    for (var q = 1; q < pts.length; q++) gcPath(pts[q-1][0], pts[q-1][1], pts[q][0], pts[q][1], 1, 24);
                    c.setLineDash([]);
                    for (var q2 = 0; q2 < pts.length; q2++) {
                        var pp = P(pts[q2][0], pts[q2][1]);
                        c.fillStyle = "rgba(90,220,255,0.9)";
                        c.beginPath(); c.arc(pp[0], pp[1], 2.6, 0, Math.PI * 2); c.fill();
                        var lbl = q2 === 0 ? "T.A.R. CORE" : (q2 <= hops ? mc.route[q2-1][0].toUpperCase() : "");
                        if (lbl) c.fillText(lbl, pp[0] + 5, pp[1] - 4);
                    }
                    // breach: pulse over the target
                    if (mc.phase === "breach" || mc.phase === "lock") {
                        var tp = P(mc.tlat, mc.tlon);
                        for (var r2 = 0; r2 < 3; r2++) {
                            var f = (mc.t * 2 + r2 / 3) % 1;
                            c.strokeStyle = "rgba(255,42,61," + (0.6 * (1 - f)) + ")"; c.lineWidth = 1.5;
                            c.beginPath(); c.arc(tp[0], tp[1], 6 + f * 60, 0, Math.PI * 2); c.stroke();
                        }
                    }
                    if (mc.phase === "lock" || mc.phase === "flight" || mc.phase === "impact") {
                        c.strokeStyle = "rgba(255,42,61,0.3)"; c.lineWidth = 1; c.setLineDash([2, 5]);
                        gcPath(mc.silo[1], mc.silo[2], mc.tlat, mc.tlon, 1, 60);
                        c.setLineDash([]);
                        var sp = P(mc.silo[1], mc.silo[2]);
                        c.fillStyle = "#ff2a3d"; c.fillRect(sp[0] - 3, sp[1] - 3, 6, 6);
                        c.fillText(mc.silo[0], sp[0] + 6, sp[1] + 3);
                    }
                    if (mc.phase === "flight" || mc.phase === "impact") {
                        var tt = mc.phase === "impact" ? 1 : mc.flight;
                        c.strokeStyle = "rgba(255,190,90,0.95)"; c.lineWidth = 2;
                        var hd = gcPath(mc.silo[1], mc.silo[2], mc.tlat, mc.tlon, tt, 80);
                        if (mc.phase === "flight" && hd) {
                            var gr = c.createRadialGradient(hd[0], hd[1], 0, hd[0], hd[1], 11);
                            gr.addColorStop(0, "rgba(255,245,210,1)"); gr.addColorStop(1, "rgba(255,90,40,0)");
                            c.fillStyle = gr; c.beginPath(); c.arc(hd[0], hd[1], 11, 0, Math.PI * 2); c.fill();
                        }
                    }
                    if (mc.phase === "impact") {
                        var ip = P(mc.tlat, mc.tlon);
                        var fl = c.createRadialGradient(ip[0], ip[1], 0, ip[0], ip[1], 14 + mc.boom * 34);
                        fl.addColorStop(0, "rgba(255,255,235," + (1 - mc.boom) + ")");
                        fl.addColorStop(1, "rgba(255,70,20,0)");
                        c.fillStyle = fl; c.beginPath(); c.arc(ip[0], ip[1], 14 + mc.boom * 34, 0, Math.PI * 2); c.fill();
                        for (var ring = 0; ring < 3; ring++) {
                            var b = Math.max(0, mc.boom - ring * 0.18);
                            c.strokeStyle = "rgba(255,200,120," + (0.9 * (1 - b)) + ")"; c.lineWidth = 2;
                            c.beginPath(); c.arc(ip[0], ip[1], 4 + b * 40, 0, Math.PI * 2); c.stroke();
                        }
                    }
                }
            }
        }
        // target reticle (outside the zoom, stays crisp)
        Item {
            visible: mc.target !== "" && mc.phase !== "route" && mc.phase !== "idle"
            x: mapBox.px(mc.tlon) - width / 2
            y: mapBox.py(mc.tlat) - height / 2
            width: mc.s(mc.phase === "breach" ? 70 : 40); height: width
            Behavior on width { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
            Rectangle {
                anchors.centerIn: parent
                width: parent.width; height: width; radius: width / 2
                color: "transparent"; border.width: 1.5; border.color: mc.red
                SequentialAnimation on opacity {
                    loops: Animation.Infinite; running: mc.open && mc.phase !== "impact"
                    NumberAnimation { to: 0.35; duration: 420 }
                    NumberAnimation { to: 1.0; duration: 420 }
                }
            }
            Rectangle { anchors.centerIn: parent; width: parent.width * 1.5; height: 1; color: mc.red; opacity: 0.8 }
            Rectangle { anchors.centerIn: parent; width: 1; height: parent.height * 1.5; color: mc.red; opacity: 0.8 }
            Text {
                x: parent.width + mc.s(4); y: -mc.s(12)
                text: mc.city.toUpperCase() + (mc.phase === "breach" ? "  [BREACHING]" : "  [LOCKED]") + "\n"
                      + mc.fmtLat(mc.tlat) + " " + mc.fmtLon(mc.tlon)
                color: mc.red
                font.family: "JetBrains Mono"; font.pixelSize: mc.s(9); font.bold: true
            }
        }
        Rectangle {                         // white-out on impact
            anchors.fill: parent
            color: "#fff4e6"
            opacity: mc.phase === "impact" ? Math.max(0, 0.55 - mc.boom * 1.4) : 0
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: mc.s(26)
            visible: mc.phase === "impact" && mc.boom > 0.3
            text: "IMPACT CONFIRMED · " + mc.city.toUpperCase()
            color: mc.red
            font.family: "JetBrains Mono"; font.pixelSize: mc.s(16); font.bold: true; font.letterSpacing: mc.s(3)
            opacity: Math.min(1, (mc.boom - 0.3) * 3)
        }
        Text {
            anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: mc.s(6)
            text: "PROJ EQUIRECT · NE-110M · ZOOM " + mc.zoom.toFixed(1) + "x"
            color: mc.dim; font.family: "JetBrains Mono"; font.pixelSize: mc.s(8)
        }
        Text {
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: mc.s(6)
            text: "// SIMULATION"
            color: mc.dim; font.family: "JetBrains Mono"; font.pixelSize: mc.s(8)
        }
        Rectangle {                         // scanline sweep
            width: parent.width; height: mc.s(2); color: mc.red; opacity: 0.08
            NumberAnimation on y { running: mc.open; loops: Animation.Infinite; from: 0; to: mapBox.height; duration: 3200 }
        }
    }

    // ---- right column: telemetry + log
    Rectangle {
        id: teleBox
        anchors.left: mapBox.right; anchors.right: parent.right; anchors.top: head.bottom
        anchors.margins: mc.s(14)
        height: tele.implicitHeight + mc.s(18)
        color: "#080203"; border.width: 1; border.color: mc.dim
        Column {
            id: tele
            anchors.left: parent.left; anchors.top: parent.top; anchors.margins: mc.s(9)
            spacing: mc.s(3)
            Repeater {
                model: [["TARGET ", mc.target ? mc.city.toUpperCase() : "--"],
                        ["COUNTRY", mc.country ? mc.country.toUpperCase() : "--"],
                        ["SILO   ", mc.target ? mc.silo[0] : "--"],
                        ["RANGE  ", mc.target ? Math.round(mc.distKm) + " km" : "--"],
                        ["ALT    ", mc.phase === "flight" ? Math.round(mc.altKm) + " km" : "--"],
                        ["VEL    ", mc.phase === "flight" ? "MACH " + mc.mach.toFixed(1) : "--"],
                        ["WARHEAD", mc.target ? "XR-77 HELLFIRE" : "--"]]
                Text {
                    required property var modelData
                    text: modelData[0] + "  " + modelData[1]
                    color: modelData[1] === "--" ? "#6b3a40" : "#e6c9cd"
                    font.family: "JetBrains Mono"; font.pixelSize: mc.s(9)
                }
            }
        }
    }
    Rectangle {
        anchors.left: mapBox.right; anchors.right: parent.right
        anchors.top: teleBox.bottom; anchors.bottom: parent.bottom
        anchors.leftMargin: mc.s(14); anchors.rightMargin: mc.s(14)
        anchors.topMargin: mc.s(10); anchors.bottomMargin: mc.s(14)
        color: "#080203"; border.width: 1; border.color: mc.dim
        clip: true
        Column {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: mc.s(9)
            spacing: mc.s(4)
            Repeater {
                model: mc.log
                Text {
                    required property var modelData
                    width: parent.width
                    text: modelData
                    wrapMode: Text.WrapAnywhere
                    color: /LAUNCHED|LOCKED|IMPACT|fall in|DOWN/.test(modelData) ? mc.red
                         : /hop /.test(modelData) ? "#7fd8ff" : "#d9b9be"
                    font.family: "JetBrains Mono"; font.pixelSize: mc.s(9)
                    font.bold: /LAUNCHED|IMPACT/.test(modelData)
                }
            }
        }
    }
}
