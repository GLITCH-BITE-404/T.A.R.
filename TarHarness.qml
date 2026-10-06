// T.A.R. standalone launcher.
//   qs -p ~/.config/hypr/scripts/quickshell/TarHarness.qml
//
// A REAL toplevel window, not a layershell overlay: Hyprland tiles, floats,
// moves and closes it like anything else, there is no keyboard grab, and it
// never sits above other windows swallowing clicks.
//
// Start staging is driven by `start_mode` (tar_caps.py start-mode ...):
//   window     - open straight at working size
//   cinematic  - open screen-sized for the intro, then settle to a window
//   floating   - same, and ask the compositor to float it
//
// Sizing resizes THIS window, never `hyprctl dispatch fullscreen`. That
// dispatcher acts on whatever window is ACTIVE, which during startup is the
// terminal you launched from -- it will happily fullscreen the wrong window.
import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
    FloatingWindow {
        id: win

        title: "T.A.R."
        // small minimum: when Hyprland tiles T.A.R. narrower than this, it kept
        // drawing at 620 px and the right side was cropped off
        minimumSize.width: 340
        minimumSize.height: 420
        color: "transparent"

        readonly property int workW: 1040
        readonly property int workH: 760
        property bool settled: false

        implicitWidth: workW
        implicitHeight: workH

        // Closed from the compositor (your close keybind): with the wake word on,
        // T.A.R. keeps listening in the background and "hey tar" brings this
        // window back; otherwise closing quits like before.
        property bool reopening: false
        onVisibleChanged: {
            if (visible || reopening) return;
            if (loader.item && loader.item.wakeOn) loader.item.backgrounded = true;
            else Qt.quit();
        }

        // Every dispatch below is targeted BY TITLE. Untargeted dispatchers act
        // on the active window, which at startup is the terminal you launched
        // from -- targeting by title makes hitting the wrong window impossible.
        readonly property string sel: "title:^(T\\.A\\.R\\.)$"

        function hypr(args) {
            Quickshell.execDetached(["hyprctl", "dispatch"].concat(args));
        }
        property bool floated: false
        property int pendW: 0
        property int pendH: 0
        function sizeTo(w, h) {
            // A TILED window ignores the app's requested size -- the compositor
            // owns its geometry -- so float first. The resize must NOT be sent
            // in the same batch: applying `setfloating` restores Hyprland's own
            // remembered floating size, which lands after our resize and undoes
            // it. Hence the deferral.
            var sc = win.screen;
            win.pendW = Math.round(sc ? Math.min(w, sc.width * 0.98) : w);
            win.pendH = Math.round(sc ? Math.min(h, sc.height * 0.94) : h);
            if (!win.floated) {
                // Only once. Re-issuing setfloating on an already-floating
                // window makes Hyprland re-apply its remembered floating
                // geometry, which lands after our resize and undoes it.
                win.floated = true;
                win.hypr(["setfloating", win.sel]);
                resizeDefer.restart();
            } else {
                resizeNow.restart();
            }
        }
        Timer {
            id: resizeDefer
            interval: 160
            onTriggered: {
                win.hypr(["resizewindowpixel",
                          "exact " + win.pendW + " " + win.pendH
                          + "," + win.sel]);
                centerDefer.restart();
            }
        }
        Timer {
            id: resizeNow
            interval: 16
            onTriggered: {
                win.hypr(["resizewindowpixel",
                          "exact " + win.pendW + " " + win.pendH
                          + "," + win.sel]);
                centerDefer.restart();
            }
        }
        Timer {
            id: centerDefer
            interval: 90
            onTriggered: win.hypr(["centerwindow", win.sel])
        }
        Timer {
            id: reapply; interval: 120
            onTriggered: win.sizeTo(win.pendW > 0 ? win.pendW : win.workW,
                                    win.pendH > 0 ? win.pendH : win.workH)
        }
        function goBig() {
            var sc = win.screen;
            win.sizeTo(sc ? sc.width * 0.96 : 1600,
                       sc ? sc.height * 0.94 : 900);
        }
        // ---- watch our own window in Hyprland (found by process id, so a
        // second T.A.R. can't confuse it):
        //  * start mode floating but it came up tiled -> float it back
        //    (only in the first seconds after it appears, so a manual tile sticks)
        //  * tiled and squeezed by other windows (a terminal etc.) -> widen
        //    our tile instead of cramming the UI into a sliver
        readonly property int comfyW: 560
        property bool tiled: false              // from the watcher
        property real enforceUntil: 0
        Process {
            id: geomProbe
            command: ["sh", "-c", "hyprctl clients -j | jq -c --argjson p " + Quickshell.processId
                      + " '[.[] | select(.pid == $p)][0] // {}'"]
            stdout: StdioCollector { onStreamFinished: win.checkGeom(this.text) }
        }
        Timer {
            interval: 2000; repeat: true
            running: win.settled && win.visible
            onTriggered: if (!geomProbe.running) geomProbe.running = true
        }
        function checkGeom(t) {
            var d;
            try { d = JSON.parse(t); } catch (e) { return; }
            if (!d || !d.address || !d.size) return;
            win.tiled = !d.floating;
            var mode = loader.item ? loader.item.startMode : "floating";
            if (mode === "floating" && !d.floating && Date.now() < win.enforceUntil) {
                win.floated = false;
                win.sizeTo(win.pendW > 0 ? win.pendW : win.workW, win.pendH > 0 ? win.pendH : win.workH);
                return;
            }
            if (!d.floating && d.size[0] < win.comfyW) {
                win.hypr(["resizewindowpixel", (win.comfyW - d.size[0]) + " 0,address:" + d.address]);
            }
        }

        function settle() {
            win.enforceUntil = Date.now() + 15000;
            if (win.settled) return;
            win.settled = true;
            win.sizeTo(win.workW, win.workH);
            if (loader.item && loader.item.startMode !== "floating") {
                // "cinematic" ends as a normal tiled window; "floating" stays
                // floating where the intro left it.
                win.hypr(["settiled", win.sel]);
            }
        }

        Loader {
            id: loader
            anchors.fill: parent
            source: "tar/tar.qml"
            focus: true
            onLoaded: {
                item.hostHandlesClose = true;
                item.windowed = true;
                item.closed.connect(function () {
                    // wake word on: hide and keep listening ("hey tar" brings it
                    // back); everything else was already stopped by shutdown()
                    if (item.wakeOn) { item.backgrounded = true; win.reopening = true;
                                       win.visible = false; win.reopening = false; }
                    else Qt.quit();
                });
                item.showRequested.connect(function () {
                    item.backgrounded = false;
                    // after a compositor close `visible` can still read true while
                    // the surface is gone -- cycle it to really map a new one
                    win.reopening = true;
                    win.visible = false;
                    win.visible = true;
                    win.reopening = false;
                    // a freshly mapped window is TILED by Hyprland -- re-apply
                    // the start mode (it came back squished as a tile before)
                    win.floated = false;
                    win.enforceUntil = Date.now() + 15000;
                    if (item.startMode !== "window") reapply.restart();
                });
                // you choose floating vs tiled; T.A.R. never flips it on its own
                // after startup (resizing used to float a tiled window)
                item.floatRequested.connect(function (fl) {
                    if (fl) {
                        win.tiled = false;
                        win.floated = false;            // let sizeTo float + size + centre it
                        win.sizeTo(win.workW, win.workH);
                    } else {
                        win.tiled = true;
                        win.floated = false;
                        win.hypr(["settiled", win.sel]);
                    }
                });
                item.sizeRequested.connect(function (w, h) {
                    if (!win.settled) return;   // don't fight the intro
                    // tiled: the layout owns the size -- resizing here is what made
                    // T.A.R. go thinner when a tab closed. The watcher only ever
                    // WIDENS a squeezed tile.
                    if (win.tiled) return;
                    win.sizeTo(w, h);
                });
                modeProbe.start();
            }
        }

        // tar.qml learns start_mode from tar_caps.py asynchronously, so give it
        // a beat before deciding whether to play the intro at all.
        Timer {
            id: modeProbe
            interval: 150
            property int waited: 0
            onTriggered: {
                // wait (up to ~3s) until T.A.R. has actually read start_mode
                if (loader.item && !loader.item.startModeKnown && waited < 20) {
                    waited++; restart(); return;
                }
                var m = loader.item ? loader.item.startMode : "cinematic";
                if (m === "window") { win.settle(); return; }
                win.goBig();
                settleTimer.start();
            }
        }
        Timer {
            id: settleTimer
            interval: 2200
            onTriggered: win.settle()
        }

        // Give the shutdown call a moment to reach ollama before the process
        // dies -- Qt.quit() would otherwise kill the reaper mid-spawn.
        Timer { id: quitDefer; interval: 350; onTriggered: Qt.quit() }

        Item {
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: (e) => {
                if (loader.item) loader.item.shutdown();
                quitDefer.start();          // let the reaper actually spawn
                e.accepted = true;
            }
        }
    }
}
