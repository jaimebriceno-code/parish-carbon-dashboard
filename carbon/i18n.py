"""The English and Spanish text of the site: `STRINGS`, `t()` and `parity_errors()`.

How the table is organised
--------------------------
Each row of `_ROWS` is `(key, English, Spanish)`, so the two languages sit side by side and cannot drift
apart silently. Keys are grouped by page with a prefix: `site_`, `nav_`, `footer_` and `lang_` for the page
chrome; `home_`, `chart_`, `travel_`, `data_`, `opendata_`, `method_`, `about_` for the pages; `col_`, `unit_`,
`fuel_` and `dq_` (the data-quality labels) for the tables; `factor_name_<factor_id>`; `month_short_01` to
`month_short_12`. To add a string, add one row; to add a page, give its keys a new prefix.

Nothing here names a parish. Wherever the text speaks of the parish, its place, its campus, its travel line, its
grid subregion or its baseline year, the string holds one of the placeholders in `ORG_PLACEHOLDERS`, and the site
fills them from `parish.toml` (`carbon.settings.Settings.words`). `{Campus}` is `{campus}` with a capital first
letter, for the start of a sentence. Both tables are always complete; which languages are built is the
parish's choice (`[site] languages`).

Rules the tests enforce on every row, now and as the table grows:

- Strings are plain text. Templates autoescape, so markup in a string would be shown literally. A link or a
  bold label is built in the template from separate keys (see `about_eco_intro` and `about_eco_link`).
- Placeholders are named, `{like_this}`, never positional. Both languages use the same ones, and `t()` raises
  KeyError when a value is not supplied. Numbers and dates are formatted by the caller before they are
  passed in, so each page decides its own decimals and separators.
- The offset rule: the words "offset", "compensación", "compensar" and "compensa" appear only inside the two
  allowed phrases in `ALLOWED_OFFSET_PHRASES`. The avoided-emissions text must say, in English, "avoided
  emissions compared with a gasoline car, not an offset" and, in Spanish, "emisiones evitadas en comparación
  con un auto de gasolina, no una compensación". Anything else that says "offset" is a failure.
- Public wording never mentions solar outside the `about_solar_` keys, which the About page shows only once a
  dated `solar_connected` milestone exists. No dollar amounts, long digit runs, leases or vendors, and no
  utility, diocese or other proper name: those belong to one parish, not to the template.
- Spanish is translated, not copied: a Spanish string identical to its English one must be listed in
  `SAME_IN_BOTH_LANGUAGES` in tests/test_i18n.py (proper names, units and symbols).

`parity_errors()` reports every way the two languages disagree. The build refuses to run while it is not empty.
"""

from __future__ import annotations

import re
import string
import unicodedata

LANGUAGES: tuple[str, ...] = ("en", "es")

# Placeholders filled from parish.toml on every string, so no row needs to be passed them explicitly.
ORG_PLACEHOLDERS: frozenset[str] = frozenset(
    {"name", "city", "state", "zip", "campus", "Campus", "travel_label", "subregion", "year"}
)

# The only places the word "offset" (or its Spanish forms) may appear. Imported by tests/test_i18n.py and by the
# build's forbidden-content scan, so both check the same rule.
ALLOWED_OFFSET_PHRASES: tuple[str, ...] = ("not an offset", "no una compensación")
OFFSET_TERMS: tuple[str, ...] = ("offset", "compensación", "compensar", "compensa")


def offset_terms_outside_allowed(text: str) -> list[str]:
    """The offset words found in `text` once every allowed phrase is removed (case-insensitive); [] is clean."""
    remaining = unicodedata.normalize("NFC", text)
    for phrase in ALLOWED_OFFSET_PHRASES:
        remaining = re.sub(re.escape(phrase), "", remaining, flags=re.IGNORECASE)
    remaining = remaining.lower()
    return [term for term in OFFSET_TERMS if term in remaining]


_ROWS: tuple[tuple[str, str, str], ...] = (
    # --- page chrome ---------------------------------------------------------------------------------------
    ("site_name", "{name}", "{name}"),
    ("site_tagline", "Carbon dashboard · {city}, {state}", "Panel de carbono · {city}, {state}"),
    ("page_title", "{page} - {site}", "{page} - {site}"),
    ("skip_to_content", "Skip to content", "Saltar al contenido"),
    ("lang_switch_label", "Language / Idioma", "Language / Idioma"),
    ("lang_name_en", "English", "English"),
    ("lang_name_es", "Español", "Español"),
    ("nav_label", "Sections", "Secciones"),
    ("nav_home", "Our footprint", "Nuestra huella"),
    ("nav_data", "Data", "Datos"),
    ("nav_methodology", "Methodology", "Metodología"),
    ("nav_about", "About", "Acerca de"),
    (
        "footer_text",
        "{name} · {city}, {state} {zip} · {contact} · Data license {license}",
        "{name} · {city}, {state} {zip} · {contact} · Licencia de datos {license}",
    ),
    ("updated_on", "Data last updated {date}", "Datos actualizados el {date}"),
    # Shown on every page, and flagged in the feed, until parish.toml says sample_data = false.
    ("sample_banner_lead", "Sample data.", "Datos de ejemplo."),
    (
        "sample_banner",
        "The numbers on this site are made up to show how the dashboard works. They are not this parish's real "
        "energy use.",
        "Las cifras de este sitio son inventadas para mostrar cómo funciona el panel. No son el consumo real de "
        "energía de esta parroquia.",
    ),
    # --- shared labels for tables and tiles ----------------------------------------------------------------
    ("fuel_gas", "Natural gas", "Gas natural"),
    ("fuel_electricity", "Electricity", "Electricidad"),
    ("unit_therms", "therms", "therms"),
    ("unit_kwh", "kWh", "kWh"),
    ("period_range", "{first} to {last}", "{first} a {last}"),
    ("col_month", "Month", "Mes"),
    ("col_fuel", "Fuel", "Combustible"),
    ("col_quantity", "Amount", "Cantidad"),
    ("col_unit", "Unit", "Unidad"),
    ("col_kwh", "kWh", "kWh"),
    ("col_therms", "Therms", "Therms"),
    ("col_tco2e", "t CO₂e", "t CO₂e"),
    ("col_scope", "Scope", "Alcance"),
    ("col_source", "Source", "Fuente"),
    ("col_miles", "Miles", "Millas"),
    ("col_travel_tco2e", "t CO₂e from charging", "t CO₂e por la carga"),
    (
        "col_avoided",
        "Avoided emissions, t CO₂e (not an offset)",
        "Emisiones evitadas, t CO₂e (no una compensación)",
    ),
    ("col_factor", "Factor", "Factor"),
    ("col_value", "Value", "Valor"),
    ("col_edition", "Edition", "Edición"),
    ("col_revision", "Revision", "Revisión"),
    ("col_retrieved", "Retrieved", "Fecha de consulta"),
    ("col_link", "Source link", "Enlace a la fuente"),
    ("row_total", "Total", "Total"),
    ("table_toggle", "Show the numbers as a table", "Ver las cifras en una tabla"),
    # --- data-quality labels: the four kinds of campus reading and the two kinds of travel entry -----------
    ("dq_bill", "Exact, from the bill", "Exacto, de la factura"),
    ("dq_utility_export", "Exact, from the utility's data file", "Exacto, del archivo de datos de la empresa de servicios"),
    ("dq_chart_estimate", "Estimate: figure printed on a bill chart", "Estimación: cifra impresa en un gráfico de la factura"),
    ("dq_chart_measured", "Estimate: measured from a bill chart", "Estimación: medida de un gráfico de la factura"),
    ("dq_uncertainty", "give or take about {pct}%", "con un margen de alrededor de {pct} %"),
    ("dq_exact", "Exact, from the bill", "Exacto, de la factura"),
    ("dq_estimated", "Estimated from a bill chart", "Estimado de un gráfico de la factura"),
    ("travel_dq_estimate", "estimate from the stated schedule", "estimación según el calendario indicado"),
    ("travel_dq_odometer", "from odometer readings", "de lecturas del odómetro"),
    # --- Home ----------------------------------------------------------------------------------------------
    ("home_h1", "Our carbon footprint", "Nuestra huella de carbono"),
    (
        "home_intro",
        "How much heat and electricity {campus} uses, and the greenhouse gases that causes. "
        "An honest starting point, updated as each bill arrives.",
        "Cuánta calefacción y electricidad usa {campus}, y los gases de efecto invernadero "
        "que eso produce. Un punto de partida honesto, actualizado con cada factura.",
    ),
    ("home_eyebrow_provisional", "Provisional baseline · {period}", "Línea base provisional · {period}"),
    ("home_eyebrow_final", "Baseline · {period}", "Línea base · {period}"),
    ("home_tonnes_unit", "tonnes CO₂e", "toneladas de CO₂e"),
    (
        "home_lead",
        "{Campus}, from natural gas heating and purchased electricity, over twelve billing months.",
        "{Campus}, por calefacción de gas natural y electricidad comprada, en doce meses de facturación.",
    ),
    ("home_badge_provisional_estimated", "Provisional: {pct}% estimated", "Provisional: {pct} % estimado"),
    (
        "home_badge_provisional",
        "Provisional: exact figures, not yet a calendar-year baseline",
        "Provisional: cifras exactas, aún no una línea base de año calendario",
    ),
    ("home_badge_final", "Final: exact figures from the bills", "Final: cifras exactas de las facturas"),
    ("home_badge_estimated", "{pct}% estimated", "{pct} % estimado"),
    ("home_why_lead", "Why provisional?", "¿Por qué provisional?"),
    (
        "home_why_estimates",
        "Figures marked as estimates are read from the small charts printed on our bills. "
        "Exact figures from the bills will replace them, and the label will change.",
        "Las cifras marcadas como estimaciones se leen de los pequeños gráficos impresos "
        "en nuestras facturas. Las cifras exactas de las facturas las reemplazarán y la etiqueta cambiará.",
    ),
    (
        "home_why_window",
        "This baseline covers the latest twelve billing months for which we have both gas and "
        "electricity. When we have every bill for calendar year {year}, we will re-base to that year.",
        "Esta línea base cubre los últimos doce meses de facturación de los que tenemos "
        "gas y electricidad. Cuando tengamos todas las facturas del año calendario {year}, cambiaremos la línea "
        "base a ese año.",
    ),
    ("home_nobaseline_heading", "No complete baseline yet", "Todavía no hay una línea base completa"),
    (
        "home_nobaseline_body",
        "A baseline needs twelve consecutive billing months of both natural gas and electricity. We do not have "
        "them all yet, and a partial total would understate our footprint, so we are not showing one. "
        "We add figures as each bill arrives.",
        "Una línea base necesita doce meses de facturación consecutivos de gas natural y de electricidad. "
        "Todavía no los tenemos todos, y un total parcial subestimaría nuestra huella, así que no mostramos "
        "ninguno. Agregamos las cifras a medida que llega cada factura.",
    ),
    (
        "home_split_label",
        "Natural gas {gas} percent, electricity {electricity} percent",
        "Gas natural {gas} por ciento, electricidad {electricity} por ciento",
    ),
    ("home_tile_gas_detail", "{therms} therms · {share}% of the total", "{therms} therms · {share} % del total"),
    ("home_tile_electricity_detail", "{kwh} kWh · {share}% of the total", "{kwh} kWh · {share} % del total"),
    ("home_month_heading", "Month by month", "Mes a mes"),
    ("home_legend_estimated", "Dashed outline = estimated", "Contorno discontinuo = estimado"),
    ("home_legend_exact", "Solid outline = exact, from the bill", "Contorno sólido = exacto, de la factura"),
    ("home_swipe", "Swipe sideways to see every month.", "Deslice hacia los lados para ver todos los meses."),
    (
        "home_table_caption",
        "Energy use and emissions by billing month (the month the billing period ends)",
        "Consumo de energía y emisiones por mes de facturación (el mes en que termina el período)",
    ),
    ("milestones_heading", "Milestones", "Hitos"),
    # --- charts --------------------------------------------------------------------------------------------
    ("chart_title_monthly", "Monthly emissions", "Emisiones mensuales"),
    (
        "chart_desc_monthly",
        "Stacked bars of tonnes CO₂e for each billing month, split into natural gas and electricity. "
        "Dashed outlines mark estimated months.",
        "Barras apiladas de toneladas de CO₂e por mes de facturación, divididas en gas natural y electricidad. "
        "Los contornos discontinuos marcan los meses estimados.",
    ),
    ("chart_no_data", "No data to chart yet.", "Todavía no hay datos para graficar."),
    ("chart_y_axis", "t CO₂e", "t CO₂e"),
    # --- the travel card (scope 3, category 6); its name is {travel_label}, from parish.toml ----------------
    ("travel_tag", "Shown separately · scope 3", "Se muestra aparte · alcance 3"),
    ("travel_heading", "{travel_label}", "{travel_label}"),
    ("travel_category", "Scope 3, category 6: business travel", "Alcance 3, categoría 6: viajes de negocios"),
    (
        "travel_intro",
        "Driving on church business in an electric car. This is never added to the campus total above.",
        "Viajes en auto eléctrico por asuntos de la iglesia. Nunca se suman al total del campus de arriba.",
    ),
    ("travel_stat_miles", "miles in twelve months ({quality})", "millas en doce meses ({quality})"),
    (
        "travel_stat_charging",
        "CO₂e from charging, counted on this line only",
        "CO₂e por la carga, contado solo en esta línea",
    ),
    (
        "travel_stat_avoided",
        "avoided emissions compared with a gasoline car, not an offset",
        "emisiones evitadas en comparación con un auto de gasolina, no una compensación",
    ),
    (
        "travel_avoided_none",
        "No emissions avoided compared with a gasoline car in this period.",
        "No hay emisiones evitadas en comparación con un auto de gasolina en este período.",
    ),
    (
        "travel_not_netted",
        "The avoided figure is for information only. It is never subtracted from the campus total.",
        "La cifra de emisiones evitadas es solo informativa. Nunca se resta del total del campus.",
    ),
    (
        "travel_basis",
        "Based on the EPA fuel economy label for the vehicle: {kwh100} kWh per 100 miles, at the same grid factor "
        "as the campus electricity (see Methodology).",
        "Basado en la etiqueta de economía de combustible de la EPA para el vehículo: {kwh100} kWh por 100 millas, "
        "con el mismo factor de la red que la electricidad del campus (vea Metodología).",
    ),
    (
        "travel_miles_estimate_note",
        "Miles are an estimate at {miles_per_day} miles per working day and will be replaced by odometer readings.",
        "Las millas son una estimación de {miles_per_day} millas por día laborable y se reemplazarán con lecturas "
        "del odómetro.",
    ),
    ("travel_miles_odometer_note", "Miles come from odometer readings.", "Las millas provienen de lecturas del odómetro."),
    (
        "travel_months_note",
        "Entries so far cover {n} of the twelve months.",
        "Las entradas hasta ahora cubren {n} de los doce meses.",
    ),
    ("travel_cell_already_counted", "Already in campus electricity", "Ya está en la electricidad del campus"),
    (
        "travel_charging_note",
        "Charging electricity is counted on this line, not in the campus meters.",
        "La electricidad de carga se cuenta en esta línea, no en los medidores del campus.",
    ),
    (
        "travel_already_counted",
        "Charging electricity is already counted in campus electricity, so no emissions are added on this line.",
        "La electricidad de carga ya está contada en la electricidad del campus, así que no se agregan emisiones "
        "en esta línea.",
    ),
    (
        "travel_table_caption",
        "{travel_label}, by month (scope 3, shown separately from the campus total)",
        "{travel_label}, por mes (alcance 3, se muestra aparte del total del campus)",
    ),
    # --- Data page -----------------------------------------------------------------------------------------
    ("data_h1", "Data", "Datos"),
    (
        "data_intro",
        "The figures behind every chart on this site: energy use, emissions and where each number came from. "
        "Free to download and reuse.",
        "Las cifras detrás de cada gráfico de este sitio: consumo de energía, emisiones y el origen de cada número. "
        "De descarga y uso libres.",
    ),
    ("data_table_heading", "Monthly figures", "Cifras mensuales"),
    (
        "data_table_caption",
        "Every monthly reading, with its emissions and the source of the figure",
        "Cada lectura mensual, con sus emisiones y el origen de la cifra",
    ),
    (
        "data_none_yet",
        "No monthly figures have been entered yet.",
        "Todavía no se han ingresado cifras mensuales.",
    ),
    ("data_download_heading", "Download", "Descargar"),
    ("data_download_csv", "Download as CSV", "Descargar como CSV"),
    ("data_download_json", "Download as JSON", "Descargar como JSON"),
    ("data_file_index", "Feed index and documentation", "Índice y documentación de la fuente de datos"),
    ("data_file_monthly", "Monthly figures", "Cifras mensuales"),
    ("data_file_annual", "Twelve-month totals and the baseline", "Totales de doce meses y la línea base"),
    ("data_file_travel", "{travel_label}, monthly", "{travel_label}, por mes"),
    ("data_file_factors", "Emission factors", "Factores de emisión"),
    ("data_file_milestones", "Milestones", "Hitos"),
    ("data_file_schema", "Schemas and API description", "Esquemas y descripción de la API"),
    ("opendata_heading", "Open data", "Datos abiertos"),
    (
        "opendata_body",
        "Everything here is free to reuse with credit ({license}), so a diocese or a regional body can add it "
        "to its own reporting.",
        "Todo esto es de libre uso con crédito ({license}), para que una diócesis o un organismo regional pueda "
        "sumarlo a sus propios informes.",
    ),
    ("opendata_contact", "Questions about the data: {contact}", "Preguntas sobre los datos: {contact}"),
    # --- Methodology ---------------------------------------------------------------------------------------
    ("method_h1", "Methodology", "Metodología"),
    (
        "method_intro",
        "How we turn utility bills into tonnes of greenhouse gases, and what the numbers do and do not include.",
        "Cómo convertimos las facturas de servicios en toneladas de gases de efecto invernadero, y qué incluyen "
        "y qué no incluyen las cifras.",
    ),
    ("method_boundary_heading", "What we count", "Qué contamos"),
    (
        "method_boundary_body",
        "We count {campus}: natural gas used for heating (scope 1) and electricity we buy "
        "(scope 2). Electricity is calculated by location, using the average for the regional grid we are "
        "connected to.",
        "Contamos {campus}: el gas natural que usamos para calefacción (alcance 1) "
        "y la electricidad que compramos (alcance 2). La electricidad se calcula por ubicación, con el promedio "
        "de la red regional a la que estamos conectados.",
    ),
    ("method_factors_heading", "Emission factors", "Factores de emisión"),
    (
        "method_factors_body",
        "Emissions are the amount of energy used multiplied by an emission factor published by the US "
        "Environmental Protection Agency (EPA). All three greenhouse gases (carbon dioxide, methane and nitrous "
        "oxide) are included and expressed as CO₂e, in metric tonnes. Each factor below lists its edition, "
        "revision, source and the date we retrieved it.",
        "Las emisiones son la cantidad de energía usada multiplicada por un factor de emisión publicado por la "
        "Agencia de Protección Ambiental de EE. UU. (EPA). Se incluyen los tres gases de efecto invernadero "
        "(dióxido de carbono, metano y óxido nitroso), expresados como CO₂e, en toneladas métricas. Cada factor "
        "de abajo indica su edición, revisión, fuente y la fecha en que lo consultamos.",
    ),
    (
        "method_grid_note",
        "For electricity we use the EPA eGRID emission rate for subregion {subregion}, the regional grid that "
        "serves {city}, {state}.",
        "Para la electricidad usamos la tasa de emisión de eGRID de la EPA para la subregión {subregion}, la red "
        "regional que abastece a {city}, {state}.",
    ),
    (
        "method_factors_recalc",
        "When EPA publishes new factors, we recalculate all months with the new set and record the change in the "
        "data changelog. Changing the factors does not change the energy amounts, which stay visible next to the "
        "emissions.",
        "Cuando la EPA publica factores nuevos, recalculamos cada mes ya registrado con el conjunto nuevo y "
        "anotamos el cambio en el registro de cambios de los datos. Cambiar los factores no cambia las "
        "cantidades de energía, que se muestran junto a las emisiones.",
    ),
    ("method_factors_caption", "Emission factors used", "Factores de emisión usados"),
    # Every electricity_<subregion> factor is shown under this one name, with its subregion code.
    (
        "factor_name_electricity",
        "Electricity, regional grid {code} (scope 2)",
        "Electricidad, red regional {code} (alcance 2)",
    ),
    (
        "factor_name_natural_gas",
        "Natural gas, burned for heat (scope 1)",
        "Gas natural, quemado para calefacción (alcance 1)",
    ),
    (
        "factor_name_gasoline_car_per_mile",
        "Gasoline passenger car, per mile (scope 3, used only for the avoided-emissions comparison)",
        "Auto de pasajeros de gasolina, por milla (alcance 3, se usa solo para la comparación de emisiones evitadas)",
    ),
    ("method_billing_heading", "Billing months", "Meses de facturación"),
    (
        "method_billing_body",
        "Each month on this site is a utility billing period, labeled by the month in which it ends, with no "
        "adjustment to calendar months. Billing periods run about a month, for example from about the 25th to the "
        "25th. Gas and electricity bills often end on different days, so the months of the two fuels may not "
        "line up exactly.",
        "Cada mes de este sitio es un período de facturación de la empresa de servicios públicos, identificado "
        "por el mes en que termina, sin ajustar a meses calendario. Los períodos duran más o menos un mes, por "
        "ejemplo del 25 al 25 aproximadamente. Las facturas de gas y de electricidad a menudo terminan en días "
        "distintos, así que los meses de los dos combustibles pueden no coincidir exactamente.",
    ),
    ("method_baseline_heading", "Baseline", "Línea base"),
    (
        "method_baseline_body",
        "Our baseline is twelve consecutive billing months for which we have both natural gas and electricity. "
        "Until we have every bill for calendar year {year} it is the latest twelve months and is labeled "
        "provisional; then we re-base to that year. "
        "It is labeled estimated for as long as any month is an estimate.",
        "Nuestra línea base son doce meses de facturación consecutivos de los que tenemos gas natural y "
        "electricidad. Hasta que tengamos todas las facturas del año calendario {year}, son los últimos doce "
        "meses y se etiqueta como provisional; después cambiaremos la línea base a ese año. Se etiqueta como "
        "estimada mientras algún mes sea una estimación.",
    ),
    ("method_labels_heading", "Exact figures and estimates", "Cifras exactas y estimaciones"),
    (
        "method_labels_intro",
        "Every monthly figure says where it came from:",
        "Cada cifra mensual indica de dónde viene:",
    ),
    (
        "method_dq_bill",
        "Exact: the amount on the bill for that period.",
        "Exacta: la cantidad que aparece en la factura de ese período.",
    ),
    (
        "method_dq_utility_export",
        "Exact: from a data file supplied by the utility.",
        "Exacta: de un archivo de datos proporcionado por la empresa de servicios.",
    ),
    (
        "method_dq_chart_estimate",
        "Estimate: a number printed on the small bar chart of past months that appears on a bill.",
        "Estimación: un número impreso en el pequeño gráfico de barras de meses anteriores que aparece en una "
        "factura.",
    ),
    (
        "method_dq_chart_measured",
        "Estimate: the height of a bar on that chart, measured against its printed gridlines, with a stated "
        "margin of error.",
        "Estimación: la altura de una barra de ese gráfico, medida contra sus líneas de cuadrícula impresas, con "
        "un margen de error indicado.",
    ),
    (
        "method_dq_replaced",
        "Estimates are replaced by exact figures as soon as we have them.",
        "Las estimaciones se reemplazan por cifras exactas en cuanto las tengamos.",
    ),
    ("method_excluded_heading", "Not included", "No incluido"),
    (
        "method_excluded_body",
        "Water, refrigerants, the congregation's travel, any meters we have not yet received bills for, and any "
        "driving other than the travel line below, which is shown separately.",
        "El agua, los refrigerantes, los viajes de la congregación, cualquier medidor del que todavía no hayamos "
        "recibido facturas y cualquier viaje en auto que no sea el de la línea de viajes de abajo, que se muestra "
        "aparte.",
    ),
    (
        "method_excluded_body_no_travel",
        "Water, refrigerants, the trips people make to and from our campus, and any meters we have not yet "
        "received bills for.",
        "El agua, los refrigerantes, los desplazamientos de las personas hacia y desde nuestro campus y "
        "cualquier medidor del que todavía no hayamos recibido facturas.",
    ),
    (
        "method_travel_body",
        "Church-related driving in an electric car is reported separately as scope 3, category 6 "
        "(business travel). It is never added to the campus total or the baseline. Emissions are the miles driven "
        "times the vehicle's energy use per mile on the EPA label, times the same grid factor as the campus "
        "electricity. Miles start as estimates and are replaced by odometer readings. Charging electricity is "
        "counted on this line, not in the campus meters.",
        "Los viajes por asuntos de la iglesia en un auto eléctrico se informan aparte como alcance "
        "3, categoría 6 (viajes de negocios). Nunca se suman al total del campus ni a la línea base. Las "
        "emisiones son las millas recorridas por el consumo de energía por milla del vehículo según la etiqueta "
        "de la EPA, por el mismo factor de la red que la electricidad del campus. Las millas comienzan como "
        "estimaciones y se reemplazan con lecturas del odómetro. La electricidad de carga se cuenta en esta "
        "línea, no en los medidores del campus.",
    ),
    ("method_avoided_heading", "Avoided emissions", "Emisiones evitadas"),
    (
        "method_avoided_body",
        "The avoided figure compares the electric car with the EPA average emission rate per mile for a passenger "
        "car (the Passenger Car row of the EPA Emission Factors Hub; that row is labeled by vehicle type, not by "
        "fuel, and light trucks and SUVs are a separate row, so the comparison may understate the saving against "
        "a larger vehicle). It is for information only: a comparison, not an offset, and it is never subtracted "
        "from the campus total or the baseline.",
        "La cifra de emisiones evitadas compara el auto eléctrico con la tasa de emisión promedio de la EPA por "
        "milla para un auto de pasajeros (la fila Passenger Car del Emission Factors Hub de la EPA; esa fila se "
        "identifica por tipo de vehículo, no por combustible, y las camionetas y los SUV están en otra fila, de "
        "modo que la comparación puede subestimar el ahorro frente a un vehículo más grande). Es solo "
        "informativa: una comparación, no una compensación, y nunca se resta del total del campus ni de la "
        "línea base.",
    ),
    ("method_terms_heading", "Words we use", "Palabras que usamos"),
    (
        "method_term_co2e",
        "CO₂e: carbon dioxide equivalent. It combines carbon dioxide, methane and nitrous oxide into one number, "
        "each weighted by how strongly it warms the climate. We report metric tonnes (1,000 kilograms).",
        "CO₂e: dióxido de carbono equivalente. Combina dióxido de carbono, metano y óxido nitroso en un solo "
        "número, cada uno ponderado según cuánto calienta el clima. Informamos en toneladas métricas (mil "
        "kilogramos).",
    ),
    (
        "method_term_scope",
        "Scope 1 is emissions released directly on site, here from burning natural gas. Scope 2 is emissions from "
        "making the electricity we buy. Scope 3 is other indirect emissions, here only the church travel shown "
        "separately.",
        "El alcance 1 son las emisiones liberadas directamente en el lugar, aquí por quemar gas natural. El "
        "alcance 2 son las emisiones de generar la electricidad que compramos. El alcance 3 son otras emisiones "
        "indirectas, aquí solo los viajes por la iglesia que se muestran aparte.",
    ),
    (
        "method_term_scope_no_travel",
        "Scope 1 is emissions released directly on site, here from burning natural gas. Scope 2 is emissions from "
        "making the electricity we buy.",
        "El alcance 1 son las emisiones liberadas directamente en el lugar, aquí por quemar gas natural. El "
        "alcance 2 son las emisiones de generar la electricidad que compramos.",
    ),
    (
        "method_term_units",
        "A kWh (kilowatt-hour) is the unit electricity is sold in. A therm is the unit of heat energy natural gas "
        "is billed in (100,000 BTU).",
        "Un kWh (kilovatio-hora) es la unidad en que se vende la electricidad. Un therm es la unidad de energía "
        "térmica en que se factura el gas natural (100,000 BTU).",
    ),
    # --- About ---------------------------------------------------------------------------------------------
    ("about_h1", "About", "Acerca de"),
    ("about_why_heading", "Why we measure", "Por qué medimos"),
    (
        "about_why_body",
        "Caring for creation starts with knowing our own footprint. We cannot reduce what we do not measure, so "
        "this site is our honest starting point: the energy our buildings use, the greenhouse gases that causes, "
        "and how both change over time.",
        "Cuidar la creación empieza por conocer nuestra propia huella. No podemos reducir lo que no medimos, así "
        "que este sitio es nuestro punto de partida honesto: la energía que usan nuestros edificios, los gases de "
        "efecto invernadero que eso produce y cómo cambian con el tiempo.",
    ),
    # The link to the parish's own website; shown only when parish.toml gives one.
    ("about_website_intro", "More about us:", "Más sobre nosotros:"),
    ("about_campus_heading", "Our campus", "Nuestro campus"),
    (
        "about_campus_body",
        "{name} is in {city}, {state}. This site covers {campus}: every utility meter listed in the open data, "
        "measured together.",
        "{name} está en {city}, {state}. Este sitio abarca {campus}: todos los medidores de servicios que "
        "aparecen en los datos abiertos, medidos en conjunto.",
    ),
    ("about_updates_heading", "How we keep it current", "Cómo lo mantenemos al día"),
    (
        "about_updates_body",
        "We add figures as each utility bill arrives, usually once a month. Numbers marked as estimates are "
        "replaced by exact figures when we have them.",
        "Agregamos las cifras a medida que llega cada factura de servicios, normalmente una vez al mes. Los "
        "números marcados como estimaciones se reemplazan por cifras exactas cuando las tengamos.",
    ),
    ("about_contact_heading", "Questions", "Preguntas"),
    ("about_contact_body", "Write to us at {contact}.", "Escríbanos a {contact}."),
    # Shown only when a milestone of kind solar_connected with a date exists. No other string mentions solar.
    ("about_solar_heading", "Solar", "Energía solar"),
    (
        "about_solar_body",
        "A solar system was connected on our campus on {date}. The footprint on this site counts the electricity "
        "we buy from the grid.",
        "Se conectó un sistema solar en nuestro campus el {date}. La huella de este sitio cuenta la electricidad "
        "que compramos a la red.",
    ),
    # Used instead of about_solar_body when the milestone is known only to the month or the year.
    (
        "about_solar_body_period",
        "A solar system was connected on our campus in {date}. The footprint on this site counts the electricity "
        "we buy from the grid.",
        "Se conectó un sistema solar en nuestro campus en {date}. La huella de este sitio cuenta la electricidad "
        "que compramos a la red.",
    ),
    # --- month names, for chart and table labels ------------------------------------------------------------
    ("month_short_01", "Jan", "ene"),
    ("month_short_02", "Feb", "feb"),
    ("month_short_03", "Mar", "mar"),
    ("month_short_04", "Apr", "abr"),
    ("month_short_05", "May", "may"),
    ("month_short_06", "Jun", "jun"),
    ("month_short_07", "Jul", "jul"),
    ("month_short_08", "Aug", "ago"),
    ("month_short_09", "Sep", "sep"),
    ("month_short_10", "Oct", "oct"),
    ("month_short_11", "Nov", "nov"),
    ("month_short_12", "Dec", "dic"),
)


def _build_table(rows: tuple[tuple[str, str, str], ...]) -> dict[str, dict[str, str]]:
    """Split `(key, English, Spanish)` rows into the two language dictionaries; a repeated key is an error."""
    table: dict[str, dict[str, str]] = {"en": {}, "es": {}}
    for key, english, spanish in rows:
        if key in table["en"]:
            raise ValueError(f"duplicate string key: {key}")
        table["en"][key] = english
        table["es"][key] = spanish
    return table


STRINGS: dict[str, dict[str, str]] = _build_table(_ROWS)


def t(lang: str, key: str, **fmt: object) -> str:
    """The string `key` in `lang` ("en" or "es") with `{placeholders}` filled from `fmt`.

    Raises KeyError for an unknown language or key, and for a placeholder that `fmt` does not supply.
    Unused values in `fmt` are ignored.
    """
    return STRINGS[lang][key].format(**fmt)


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _placeholders(text: str) -> set[str] | None:
    """Placeholder names in `text`, or None if its braces are malformed. Positional ones show as "" or digits."""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError:
        return None


def parity_errors() -> list[str]:
    """Every way the English and Spanish tables disagree; an empty list means parity holds.

    Checks that both languages exist and hold the same keys, that no string is empty or has malformed or
    positional placeholders, and that each key uses the same placeholders in both languages.
    """
    errors: list[str] = []
    for lang in LANGUAGES:
        if lang not in STRINGS:
            errors.append(f"language {lang!r} is missing from STRINGS")
    for lang in sorted(set(STRINGS) - set(LANGUAGES)):
        errors.append(f"unexpected language {lang!r} in STRINGS")
    if errors:
        return errors

    en, es = STRINGS["en"], STRINGS["es"]
    for key in sorted(set(en) - set(es)):
        errors.append(f"key {key!r} is in en but missing from es")
    for key in sorted(set(es) - set(en)):
        errors.append(f"key {key!r} is in es but missing from en")

    names: dict[str, dict[str, set[str]]] = {lang: {} for lang in LANGUAGES}
    for lang in LANGUAGES:
        for key, text in sorted(STRINGS[lang].items()):
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{lang}:{key} is empty")
                continue
            found = _placeholders(text)
            if found is None:
                errors.append(f"{lang}:{key} has malformed braces")
                continue
            for name in sorted(found):
                if name == "" or name.isdigit():
                    errors.append(f"{lang}:{key} uses a positional placeholder")
                elif not _NAME.match(name):
                    errors.append(f"{lang}:{key} uses an unsupported placeholder {name!r}")
            names[lang][key] = found

    for key in sorted(set(names["en"]) & set(names["es"])):
        if names["en"][key] != names["es"][key]:
            errors.append(
                f"key {key!r}: placeholders differ (en {sorted(names['en'][key])} vs es {sorted(names['es'][key])})"
            )
    return errors
