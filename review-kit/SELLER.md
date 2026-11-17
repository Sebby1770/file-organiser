# Selling the Storage Review Kit

The buyer downloads a finished offline tool, editable report labels, two example inputs and a quick start. An editor can use it at rough cut and final delivery to explain what grew and whether a project still fits the client’s archive budget. The public demo lets a buyer try that workflow with fictional data before committing.

## Prepare the store
1. Run `python3 scripts/build_review_kit.py` from the repository root. Use **dist/Storage-Review-Kit.zip** as the digital product. GitHub CI also attaches the ZIP inside its **storage-review-kit** artifact. Download and extract the artifact before uploading the product ZIP.
2. In your own storefront, create a one-time digital download, set your price and currency, upload the ZIP, and supply accurate support, privacy and refund information. The product is MIT licensed. Describe the convenience of the ready-made kit and handoff workflow. Only promise services you can provide.
3. Run a test purchase in the provider’s test mode. Open the delivered ZIP on a clean machine, compare both examples, and confirm that the report says **+8 GiB**.
4. When ready to accept purchases, put the public HTTPS product checkout URL in **docs/review-kit/offer.js** and publish the site. The demo then displays **Buy the offline kit**. The blank default uses the existing public interest form. Never put payment credentials in this repository. The storefront handles payment and file delivery.

## Listing copy
**Storage Review Kit for project handoffs.** Show a client why the project folder grew between deliveries. Compare two local snapshots, check a GiB archive budget and send a self-contained report with your studio name. Includes an offline browser tool, sample snapshots and a quick start. Tested in desktop Chromium. No account is needed to run the kit.

## First pilot
Offer the demo to a small group of editors or photographers already delivering projects to clients. Ask them to make one handoff with their own budget and tell you whether they would send the report. Ask what they use today and how long it takes. They can answer without sharing reports or filenames. Test a one-time price with actual purchases and track refunds and support time before choosing a permanent price. A public interest response is a lead, not a sale.
