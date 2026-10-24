# Real Exchange Rate Explorer

A beginner-friendly Streamlit app for comparing monthly real and nominal exchange rates with IMF data.

## What the app does

- Lets the user choose two countries or the euro area.
- Downloads monthly CPI and exchange-rate observations from the IMF Data Portal.
- Uses data from January 1976 onward when the IMF publishes it.
- Calculates the nominal cross-rate, the real exchange rate, and a sample-based real index.
- Draws interactive Real, Nominal, or Both charts.
- Downloads every selected month and calculation as a CSV file.
- Leaves missing IMF observations blank. It does not interpolate or invent values.

## Economic definitions

Let:

- `S(t)` = units of Country 2 currency per one unit of Country 1 currency.
- `P1(t)` = Country 1 all-items CPI.
- `P2(t)` = Country 2 all-items CPI.

The app calculates:

```text
RER(t) = S(t) × P1(t) / P2(t)
```

For the dates selected by the user, it calculates the simple average over valid months only:

```text
RER* = average of RER(t)
Real Index(t) = 100 × RER(t) / RER*
```

Changing either date makes Streamlit rerun the app, so `RER*` and the Real Index are recalculated.

The IMF exchange-rate indicator used here is `XDC_USD`: local currency units per one U.S. dollar. If `FX1` and `FX2` are those rates, then:

```text
S(t) = FX2(t) / FX1(t)
```

For example, if one U.S. dollar buys 1 unit of Country 1 currency and 110 units of Country 2 currency, one unit of Country 1 currency buys 110 units of Country 2 currency.

The nominal chart shows this actual cross-rate. It is not changed to an index.

## IMF source and API choices

The app uses the IMF's public SDMX 2.1 API:

```text
https://api.imf.org/external/sdmx/2.1
```

The IMF's official API page says that the current Data Portal provides SDMX 2.1 and SDMX 3.0 APIs. The app uses SDMX 2.1 because its CSV response is simple to read and was verified with live responses on October 24, 2026.

The verified current dataflows and keys are:

- Exchange Rates dataflow: `IMF.STA:ER`, key `COUNTRY.XDC_USD.PA_RT.M`
- Consumer Price Index dataflow: `IMF.STA:CPI`, key `COUNTRY.CPI._T.IX.M`
- Euro area CPI: `G163.HICP._T.IX.M`
- Euro area exchange rate: `G163.XDC_USD.PA_RT.M`

The URL uses `latest` for the dataflow version. At verification time, that resolved to ER `4.0.1` and CPI `5.0.0`.

Official links:

- [IMF Data Portal](https://data.imf.org/)
- [IMF API information](https://data.imf.org/en/Resource-Pages/IMF-API)
- [IMF note about the reorganized IFS data](https://data.imf.org/ifs)

## Important API uncertainties and limits

- The IMF retired its old data portal and reorganized the former International Financial Statistics data. Old examples found online can point to retired endpoints or old series IDs.
- The official Swagger explorer requires a portal sign-in. The public SDMX routes used by this app did not require a key in the live checks.
- The IMF can release a new dataflow version or change a key. Using `latest` handles ordinary version updates, but a structural change could require a code update.
- Country coverage and starting dates differ. A country can have CPI data but no matching exchange-rate observation, or the reverse.
- The data are monthly here because the keys explicitly request `M`. Missing months remain missing.
- CPI base periods can differ between countries and can be rebased by the source. The RER level therefore depends on those published index scales. The Real Index is easier to compare over time because it is divided by its own selected-sample average.
- IMF observations can be revised after this README's verification example was written.
- The IMF announced maintenance from October 24 to October 26, 2026. Temporary API failures during maintenance are not evidence that a series does not exist.

## Special currency cases

The United States is handled by its IMF `XDC_USD` series, which is 1 U.S. dollar per U.S. dollar.

For countries that adopted the euro, the IMF country-specific exchange-rate series normally ends when the old currency ends. The app:

1. Divides the old local-currency-per-dollar rate by the official fixed local-currency-per-euro conversion rate.
2. Uses the euro-area euro-per-dollar series from the country's adoption month onward.

This puts the whole exchange-rate history in euro-equivalent units and avoids a fake jump caused only by changing the currency unit. The rates are fixed unit conversions, not normalization or interpolation. The conversion rates and adoption dates come from the [European Central Bank](https://www.ecb.europa.eu/euro/intro/html/index.en.html). Bulgaria's January 2026 adoption is included.

Dollarized economies and other special cases use the observations the IMF publishes for their country code. The app does not replace missing IMF values with assumptions.

## Manual verification example

This example is reproducible in the app:

1. Choose **United States** as Country 1.
2. Choose **Japan** as Country 2.
3. Choose `2020-01` through `2020-02`.

The live IMF observations checked on October 24, 2026 were:

| Month | U.S. CPI P1 | Japan CPI P2 | U.S. FX1 | Japan FX2 |
|---|---:|---:|---:|---:|
| 2020-01 | 118.3052021160 | 100.5 | 1.0 | 109.2847368421 |
| 2020-02 | 118.6294314979 | 100.3 | 1.0 | 109.9735294118 |

January calculation:

```text
S = 109.2847368421 / 1.0
  = 109.2847368421 JPY per USD

RER = 109.2847368421 × 118.3052021160 / 100.5
    = 128.6462973164
```

February calculation:

```text
RER = 109.9735294118 × 118.6294314979 / 100.3
    = 130.0707604580
```

Benchmark and index:

```text
RER* = (128.6462973164 + 130.0707604580) / 2
     = 129.3585288872

January Real Index  = 100 × 128.6462973164 / 129.3585288872
                    = 99.4494127469

February Real Index = 100 × 130.0707604580 / 129.3585288872
                    = 100.5505872531
```

The two index values average to 100, apart from rounding. If the IMF revises an observation, use the newly downloaded values and repeat the same arithmetic.

## Run locally with Python 3.12

Open a terminal in the repository folder, then run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Then open the local address Streamlit shows in the terminal.

## Deploy on Streamlit Community Cloud

1. Put `app.py`, `requirements.txt`, and `README.md` in the top level of the GitHub repository.
2. Commit and push the files to GitHub.
3. Sign in at [Streamlit Community Cloud](https://share.streamlit.io/).
4. Select **Create app**.
5. Choose the `real-exchange-rate-app` repository and the branch containing the files.
6. Set the main file path to `app.py`.
7. Open **Advanced settings** and select Python 3.12.
8. Deploy the app.

No API key or Streamlit secret is needed for the public IMF routes currently used by the app.

## File layout

```text
real-exchange-rate-app/
├── app.py
├── requirements.txt
└── README.md
```
