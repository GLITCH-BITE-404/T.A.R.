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
import QtQuick

ShellRoot {
    FloatingWindow {
        id: win

        title: "T.A.R."
        minimumSize.width: 620
        minimumSize.height: 520
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
        function settle() {
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
                    if (item.startMode !== "window") reapply.restart();
                });
                item.sizeRequested.connect(function (w, h) {
                    if (!win.settled) return;   // don't fight the intro
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
