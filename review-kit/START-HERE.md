# Storage Review Kit
Unzip the kit, then open **Storage Review Kit.html** in desktop Chrome or Edge. It runs locally, including offline. No installation or account is required.

## Prepare a project handoff
1. Choose **Scan a folder**, or **Open snapshot** to load the JSON created by File Organiser's **Export storage snapshot** button. Use a project folder you can read. Folder selection depends on the browser. Snapshot import also works for an empty desktop scan.
2. Enter the project label and, optionally, your studio name and archive budget in GiB. These labels appear in the final report.
3. Choose **Save current snapshot** before the next delivery. Give the file a memorable name and keep it outside the folder you are measuring.
4. After the next delivery, scan the project again, then choose **Compare with saved snapshot**. Use the same project and scan method both times. Browser and desktop scans use different classification and visibility rules. The kit checks method and date. It cannot check project identity because snapshots omit paths.
5. **Download report** creates a self-contained HTML handoff with no scripts, file names, or paths. Review it before sharing. **Print / PDF** opens the browser's print dialog. Turn off browser headers and footers if you want to omit the page URL or local location.

## Measurements
Sizes sum the logical lengths of visible files. Compression, hard links and files moved to a different folder can affect how these totals relate to free disk space. Browser scans count the files the picker supplies and skip hidden entries. Browser permission omissions may be silent. Desktop snapshots include the number of unreadable items reported by the scanner. Category changes do not establish which individual files changed.

Open the two included example snapshots as current and baseline to try a comparison without using a real folder. Reset clears the current session. Copies you explicitly save remain on disk. The kit itself does not upload anything, read file contents during a folder scan, or save session data to browser storage.
