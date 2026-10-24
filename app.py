from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="🌍", layout="wide")

IMF_SDMX_BASE = "https://api.imf.org/external/sdmx/2.1"
DATA_START = pd.Period("1976-01", freq="M")
DATA_END = pd.Period(date.today(), freq="M")

# Each value is (first euro month, old local-currency units per euro).
# The fixed rates are the official rates published by the ECB.
EURO_ADOPTIONS: dict[str, tuple[str, float]] = {
    "AUT": ("1999-01", 13.7603),
    "BEL": ("1999-01", 40.3399),
    "DEU": ("1999-01", 1.95583),
    "ESP": ("1999-01", 166.386),
    "FIN": ("1999-01", 5.94573),
    "FRA": ("1999-01", 6.55957),
    "IRL": ("1999-01", 0.787564),
    "ITA": ("1999-01", 1936.27),
    "LUX": ("1999-01", 40.3399),
    "NLD": ("1999-01", 2.20371),
    "PRT": ("1999-01", 200.482),
    "GRC": ("2001-01", 340.750),
    "SVN": ("2007-01", 239.640),
    "CYP": ("2008-01", 0.585274),
    "MLT": ("2008-01", 0.429300),
    "SVK": ("2009-01", 30.1260),
    "EST": ("2011-01", 15.6466),
    "LVA": ("2014-01", 0.702804),
    "LTU": ("2015-01", 3.45280),
    "HRV": ("2023-01", 7.53450),
    "BGR": ("2026-01", 1.95583),
}


class IMFDataError(RuntimeError):
    """Raised when the IMF service cannot provide a usable response."""


def make_http_session() -> requests.Session:
    """Create a web session that retries short-lived IMF server errors."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_imf_response(
    url: str,
    *,
    params: dict[str, str] | None = None,
    accept: str,
) -> requests.Response:
    """Make one checked request to the IMF API."""
    try:
        response = make_http_session().get(
            url,
            params=params,
            headers={"Accept": accept},
            timeout=(10, 45),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise IMFDataError(f"The IMF API request failed: {exc}") from exc

    if response.status_code == 204 or not response.content.strip():
        raise IMFDataError("The IMF API returned an empty response.")
    return response


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_imf_country_names() -> dict[str, str]:
    """Get country labels from the IMF exchange-rate codelist."""
    url = f"{IMF_SDMX_BASE}/codelist/IMF.STA/CL_ER_COUNTRY_PUB/latest"
    response = get_imf_response(
        url,
        accept="application/vnd.sdmx.structure+xml;version=2.1",
    )

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise IMFDataError("The IMF country list was not valid XML.") from exc

    namespaces = {
        "structure": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
        "common": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
    }
    xml_language = "{http://www.w3.org/XML/1998/namespace}lang"
    valid_iso_codes = {country.alpha_3 for country in pycountry.countries}
    valid_iso_codes.add("KOS")  # IMF code for Kosovo; not in pycountry's ISO list.

    target = next(
        (
            item
            for item in root.findall(".//structure:Codelist", namespaces)
            if item.get("id") == "CL_ER_COUNTRY_PUB"
        ),
        None,
    )
    if target is None:
        raise IMFDataError("The IMF country codelist was missing from the response.")

    countries: dict[str, str] = {}
    for code_node in target.findall("structure:Code", namespaces):
        code = code_node.get("id", "")
        if code not in valid_iso_codes:
            continue
        english_name = next(
            (
                node.text
                for node in code_node.findall("common:Name", namespaces)
                if node.get(xml_language) == "en" and node.text
            ),
            None,
        )
        if english_name:
            countries[code] = english_name

    if not countries:
        raise IMFDataError("No countries were found in the IMF country codelist.")
    return countries


def fallback_country_names() -> dict[str, str]:
    """Provide a local country list if IMF metadata is briefly unavailable."""
    countries = {country.alpha_3: country.name for country in pycountry.countries}
    countries["KOS"] = "Kosovo, Republic of"
    return countries


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_imf_series(
    dataset: str,
    key: str,
    start_period: str,
    end_period: str,
    value_name: str,
) -> pd.DataFrame:
    """Download one IMF monthly series and return month/value columns."""
    url = f"{IMF_SDMX_BASE}/data/IMF.STA,{dataset},latest/{key}"
    response = get_imf_response(
        url,
        params={"startPeriod": start_period, "endPeriod": end_period},
        accept="text/csv",
    )

    try:
        raw = pd.read_csv(io.StringIO(response.text), dtype=str)
    except Exception as exc:
        raise IMFDataError("The IMF response could not be read as CSV.") from exc

    required = {"TIME_PERIOD", "OBS_VALUE"}
    if not required.issubset(raw.columns):
        raise IMFDataError("The IMF response did not contain the expected data columns.")

    raw = raw.loc[raw["TIME_PERIOD"].notna() & raw["OBS_VALUE"].notna()].copy()
    raw = raw.loc[raw["TIME_PERIOD"].str.fullmatch(r"\d{4}-M\d{2}", na=False)]
    if raw.empty:
        return pd.DataFrame(columns=["month", value_name])

    values = pd.to_numeric(raw["OBS_VALUE"], errors="coerce")
    if "SCALE" in raw.columns:
        scales = pd.to_numeric(raw["SCALE"], errors="coerce").fillna(0)
        values = values * (10.0 ** scales)

    result = pd.DataFrame(
        {
            "month": pd.to_datetime(
                raw["TIME_PERIOD"].str.replace("-M", "-", regex=False)
            ),
            value_name: values,
        }
    )
    result = (
        result.dropna()
        .drop_duplicates("month", keep="last")
        .sort_values("month")
    )
    return result.reset_index(drop=True)


def fetch_cpi(country_code: str) -> pd.DataFrame:
    """Get the monthly all-items price index for a country or the euro area."""
    if country_code == "G163":
        key = "G163.HICP._T.IX.M"
    else:
        key = f"{country_code}.CPI._T.IX.M"
    return fetch_imf_series(
        "CPI",
        key,
        str(DATA_START),
        str(DATA_END),
        "cpi",
    )


def fetch_exchange_rate(country_code: str) -> tuple[pd.DataFrame, str]:
    """Get local currency per USD, expressed in one consistent currency unit."""
    if country_code == "G163":
        euro = fetch_imf_series(
            "ER",
            "G163.XDC_USD.PA_RT.M",
            str(DATA_START),
            str(DATA_END),
            "lcu_per_usd",
        )
        return euro, "euro per U.S. dollar"

    if country_code not in EURO_ADOPTIONS:
        series = fetch_imf_series(
            "ER",
            f"{country_code}.XDC_USD.PA_RT.M",
            str(DATA_START),
            str(DATA_END),
            "lcu_per_usd",
        )
        return series, "local currency per U.S. dollar"

    adoption_text, local_units_per_euro = EURO_ADOPTIONS[country_code]
    adoption = pd.Period(adoption_text, freq="M")
    pre_euro_end = min(DATA_END, adoption - 1)
    parts: list[pd.DataFrame] = []

    if DATA_START <= pre_euro_end:
        old_currency = fetch_imf_series(
            "ER",
            f"{country_code}.XDC_USD.PA_RT.M",
            str(DATA_START),
            str(pre_euro_end),
            "lcu_per_usd",
        )
        if not old_currency.empty:
            old_currency["lcu_per_usd"] = (
                old_currency["lcu_per_usd"] / local_units_per_euro
            )
            parts.append(old_currency)

    if adoption <= DATA_END:
        euro = fetch_imf_series(
            "ER",
            "G163.XDC_USD.PA_RT.M",
            str(max(DATA_START, adoption)),
            str(DATA_END),
            "lcu_per_usd",
        )
        parts.append(euro)

    combined = (
        pd.concat(parts, ignore_index=True)
        if parts
        else pd.DataFrame(columns=["month", "lcu_per_usd"])
    )
    combined = (
        combined.drop_duplicates("month", keep="last")
        .sort_values("month")
    )
    return (
        combined.reset_index(drop=True),
        "euro-equivalent currency per U.S. dollar",
    )


def assemble_calculations(
    cpi_1: pd.DataFrame,
    cpi_2: pd.DataFrame,
    fx_1: pd.DataFrame,
    fx_2: pd.DataFrame,
) -> pd.DataFrame:
    """Put the four source series together and calculate S and the RER."""
    months = pd.DataFrame(
        {
            "month": pd.date_range(
                DATA_START.to_timestamp(),
                DATA_END.to_timestamp(),
                freq="MS",
            )
        }
    )
    frame = months
    for source, column in (
        (cpi_1, "P1_CPI"),
        (cpi_2, "P2_CPI"),
        (fx_1, "FX1_LCU_per_USD"),
        (fx_2, "FX2_LCU_per_USD"),
    ):
        renamed = source.rename(columns={source.columns[-1]: column})
        frame = frame.merge(
            renamed[["month", column]],
            on="month",
            how="left",
        )

    positive_fx = (
        (frame["FX1_LCU_per_USD"] > 0)
        & (frame["FX2_LCU_per_USD"] > 0)
    )
    frame["S_currency2_per_currency1"] = pd.NA
    frame.loc[positive_fx, "S_currency2_per_currency1"] = (
        frame.loc[positive_fx, "FX2_LCU_per_USD"]
        / frame.loc[positive_fx, "FX1_LCU_per_USD"]
    )
    frame["S_currency2_per_currency1"] = pd.to_numeric(
        frame["S_currency2_per_currency1"],
        errors="coerce",
    )

    positive_prices = (frame["P1_CPI"] > 0) & (frame["P2_CPI"] > 0)
    valid_rer = positive_fx & positive_prices
    frame["RER"] = pd.NA
    frame.loc[valid_rer, "RER"] = (
        frame.loc[valid_rer, "S_currency2_per_currency1"]
        * frame.loc[valid_rer, "P1_CPI"]
        / frame.loc[valid_rer, "P2_CPI"]
    )
    frame["RER"] = pd.to_numeric(frame["RER"], errors="coerce")
    return frame


def build_chart(
    sample: pd.DataFrame,
    chart_choice: str,
    country_1: str,
    country_2: str,
) -> go.Figure:
    """Create the requested interactive chart."""
    both = chart_choice == "Both"
    figure = make_subplots(specs=[[{"secondary_y": both}]])

    if chart_choice in {"Real", "Both"}:
        figure.add_trace(
            go.Scatter(
                x=sample["month"],
                y=sample["Real_Index"],
                name="Real index",
                mode="lines",
                connectgaps=False,
                hovertemplate=(
                    "%{x|%Y-%m}<br>Real index: %{y:.2f}<extra></extra>"
                ),
            ),
            secondary_y=False,
        )

    if chart_choice in {"Nominal", "Both"}:
        figure.add_trace(
            go.Scatter(
                x=sample["month"],
                y=sample["S_currency2_per_currency1"],
                name="Nominal S",
                mode="lines",
                connectgaps=False,
                hovertemplate=(
                    "%{x|%Y-%m}<br>Nominal S: %{y:.6g}<extra></extra>"
                ),
            ),
            secondary_y=both,
        )

    if chart_choice in {"Real", "Both"}:
        figure.update_yaxes(
            title_text="Real index (sample average = 100)",
            secondary_y=False,
        )
    elif chart_choice == "Nominal":
        figure.update_yaxes(
            title_text=f"{country_2} currency per {country_1} currency",
            secondary_y=False,
        )

    if both:
        figure.update_yaxes(
            title_text=(
                f"Nominal: {country_2} currency per {country_1} currency"
            ),
            secondary_y=True,
        )

    figure.update_layout(
        title=f"{country_1} versus {country_2}",
        xaxis_title="Month",
        hovermode="x unified",
        legend_title_text="Series",
        margin=dict(l=40, r=40, t=70, b=40),
    )
    return figure


def main() -> None:
    st.title("🌍 Real Exchange Rate Explorer")
    st.write(
        "Compare monthly real and nominal exchange rates using IMF CPI "
        "and exchange-rate data."
    )

    with st.expander("How the calculation works"):
        st.markdown(
            """
            The nominal rate **S** is Country 2 currency per one unit of
            Country 1 currency. The IMF source rate is local currency per
            U.S. dollar, so the app calculates **S = FX2 / FX1**. It then
            calculates **RER = S × P1 / P2**.

            The real index equals **100 × RER / `RER*`**. `RER*` is the
            simple average of all valid monthly RER values inside your
            selected dates. Missing values are left missing. They are never
            filled or interpolated.
            """
        )

    metadata_warning = None
    try:
        country_names = fetch_imf_country_names()
    except IMFDataError as exc:
        country_names = fallback_country_names()
        metadata_warning = str(exc)

    choices = {"Euro area": "G163"}
    choices.update(
        {
            f"{name} ({code})": code
            for code, name in sorted(
                country_names.items(),
                key=lambda item: item[1],
            )
        }
    )
    labels = list(choices)

    def default_position(code: str) -> int:
        return next(
            (
                i
                for i, label in enumerate(labels)
                if choices[label] == code
            ),
            0,
        )

    first_column, second_column = st.columns(2)
    with first_column:
        country_1_label = st.selectbox(
            "Country 1",
            labels,
            index=default_position("USA"),
        )
    with second_column:
        country_2_label = st.selectbox(
            "Country 2",
            labels,
            index=default_position("JPN"),
        )

    if metadata_warning:
        st.warning(
            "The IMF country list could not be refreshed, so a built-in "
            "ISO country list is being used. Data requests will still go "
            f"to the IMF. Details: {metadata_warning}"
        )

    country_1_code = choices[country_1_label]
    country_2_code = choices[country_2_label]
    country_1_name = country_1_label.rsplit(" (", 1)[0]
    country_2_name = country_2_label.rsplit(" (", 1)[0]

    try:
        with st.spinner("Loading monthly IMF data..."):
            cpi_1 = fetch_cpi(country_1_code)
            cpi_2 = fetch_cpi(country_2_code)
            fx_1, fx_1_description = fetch_exchange_rate(country_1_code)
            fx_2, fx_2_description = fetch_exchange_rate(country_2_code)
    except IMFDataError as exc:
        st.error(
            "The IMF service did not return usable data. This can be "
            "temporary, especially during IMF maintenance. Please try "
            "again later."
        )
        st.caption(str(exc))
        st.stop()

    missing_series = []
    if cpi_1.empty:
        missing_series.append(f"CPI for {country_1_name}")
    if cpi_2.empty:
        missing_series.append(f"CPI for {country_2_name}")
    if fx_1.empty:
        missing_series.append(f"exchange rate for {country_1_name}")
    if fx_2.empty:
        missing_series.append(f"exchange rate for {country_2_name}")
    if missing_series:
        st.warning(
            "No monthly IMF observations were found for: "
            + ", ".join(missing_series)
            + "."
        )

    full = assemble_calculations(cpi_1, cpi_2, fx_1, fx_2)
    has_chart_data = full[
        ["S_currency2_per_currency1", "RER"]
    ].notna().any(axis=1)

    if not has_chart_data.any():
        st.error("These selections have no overlapping monthly data to chart.")
        st.stop()

    available = full.loc[has_chart_data, "month"]
    first_available = available.min().to_period("M")
    last_available = available.max().to_period("M")
    month_periods = pd.period_range(DATA_START, DATA_END, freq="M")
    month_labels = [str(month) for month in month_periods]

    date_column_1, date_column_2 = st.columns(2)
    with date_column_1:
        start_label = st.selectbox(
            "Start month",
            month_labels,
            index=month_labels.index(str(first_available)),
        )
    with date_column_2:
        end_label = st.selectbox(
            "End month",
            month_labels,
            index=month_labels.index(str(last_available)),
        )

    start_month = pd.Period(start_label, freq="M")
    end_month = pd.Period(end_label, freq="M")
    if start_month > end_month:
        st.error("The start month must be before or equal to the end month.")
        st.stop()

    chart_choice = st.radio(
        "Show",
        ["Real", "Nominal", "Both"],
        horizontal=True,
    )

    sample = full.loc[
        (full["month"] >= start_month.to_timestamp())
        & (full["month"] <= end_month.to_timestamp())
    ].copy()

    valid_rer_count = int(sample["RER"].notna().sum())
    rer_star = (
        sample["RER"].mean()
        if valid_rer_count
        else float("nan")
    )
    sample["RER_star_sample_average"] = rer_star
    sample["Real_Index"] = (
        100.0 * sample["RER"] / rer_star
        if pd.notna(rer_star) and rer_star != 0
        else pd.NA
    )
    sample["Valid_RER_Month"] = sample["RER"].notna()

    total_months = len(sample)
    missing_rer_count = total_months - valid_rer_count
    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Valid RER months", f"{valid_rer_count:,}")
    metric_2.metric("Missing RER months", f"{missing_rer_count:,}")
    metric_3.metric(
        "RER*",
        f"{rer_star:.6g}" if pd.notna(rer_star) else "Not available",
    )

    if chart_choice in {"Real", "Both"} and not valid_rer_count:
        st.error(
            "A real index cannot be calculated because this sample has "
            "no valid RER months."
        )
    else:
        figure = build_chart(
            sample,
            chart_choice,
            country_1_name,
            country_2_name,
        )
        st.plotly_chart(figure, width="stretch")

    if missing_rer_count:
        st.info(
            f"{missing_rer_count} of {total_months} selected months lack "
            "at least one required observation. The chart leaves those "
            "RER values blank."
        )

    st.caption(
        f"Country 1 exchange-rate basis: {fx_1_description}. "
        f"Country 2 exchange-rate basis: {fx_2_description}."
    )

    download = sample.copy()
    download.insert(
        0,
        "Month",
        download.pop("month").dt.strftime("%Y-%m"),
    )
    csv_bytes = download.to_csv(
        index=False,
        float_format="%.10g",
        na_rep="",
    ).encode("utf-8")

    st.download_button(
        "Download selected sample as CSV",
        data=csv_bytes,
        file_name=(
            f"rer_{country_1_code}_{country_2_code}_"
            f"{start_label}_{end_label}.csv"
        ),
        mime="text/csv",
    )

    with st.expander("View selected data"):
        st.dataframe(download, width="stretch", hide_index=True)

    with st.sidebar:
        st.header("Data notes")
        st.markdown(
            """
            - Source: IMF Data Portal, CPI and Exchange Rates datasets.
            - Frequency: monthly.
            - Exchange rate: monthly period average, local currency per
              U.S. dollar.
            - CPI: national all-items CPI; euro area uses its all-items HICP.
            - No missing values are invented or interpolated.
            - IMF data can be revised and coverage differs by country.
            """
        )
        st.link_button("IMF Data Portal", "https://data.imf.org/")
        st.link_button(
            "IMF API information",
            "https://data.imf.org/en/Resource-Pages/IMF-API",
        )


if __name__ == "__main__":
    main()
