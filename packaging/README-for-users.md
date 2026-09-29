# Scrollstrip

Turns comics you own into a phone-friendly continuous vertical scroll.

## Running it

Unzip anywhere and run `Scrollstrip.exe`. No installation.

If nothing opens, install the Microsoft Edge WebView2 runtime:
https://developer.microsoft.com/microsoft-edge/webview2/

## Using it

1. **Import** a folder of scans, a `.cbz`, or a `.pdf`.
   CBR files are not supported - convert to CBZ first.
2. Wait for processing. The first run downloads a ~119 MB panel-detection model.
3. **Fix the boxes.** Automatic detection gets most panels and misses some.
   Pages it was unsure about are marked with `!`.
4. **Set gutters.** The gap after each panel is the pacing. `large` for a reveal
   or a scene change, `tight` for fast back-and-forth.
5. **Preview the scroll**, then **Export CBZ**.

Read the exported CBZ in any comic reader set to webtoon / continuous vertical mode.

Your chapters live in `Documents\Scrollstrip\`.

This is for comics you own.
