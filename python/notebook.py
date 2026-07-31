import glob
import json
import math
import os
import threading
import time
import zipfile
import numpy
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests as r
import solara
from IPython.core.display_functions import display
from ipydatagrid import DataGrid
from ipyleaflet import GeoJSON, LegendControl, Map
from ipystream.renderer import plotly_fig_to_html
from ipystream.utils_stacktrace import stacktrace_html, with_stacktrace
from ipystream.voila import utils_log
from ipystream.voila.spinned_print_out import get_spinner_html, spinner_template
from ipystream.voila.utils_browser_ready import on_browser_ready
from ipystream.widget_currents_children import Handle
from ipywidgets import HTML, Button, RadioButtons, widgets, Output, VBox
from ipystream.stream import Stream, WidgetCurrentsChildren
from filelock import FileLock
from concurrent.futures import ThreadPoolExecutor
from python import style
from python.utils_download_button import download_button
from python.utils_login import headers

col_value = "Capacity (kW or KWh)"
col_value_net = "Capacity (kW)"
col_name = "Name"
execution_name = "exec"
skip_download_results = True
filters_that_apply = ["Solution", "Stage"]
building_layer = "Plan_guide_V2 - adapted"
hovered_net_color = "purple"
color_palette = ["red", "orange", "purple", "brown", "pink", "cyan", "magenta", "lime", "teal", "navy"]
skip_last_level_key = "skip_last_level"
bar_path = f"iframe_figures/bar{str(time.time()).replace('.','_')}.html"

base_url = "https://eu-north-1-api.sympheny.com/"
be = f"{base_url}sympheny-app/"

# top level function in python/notebook.py must be called run()
def run():
    style.apply()
    out_username = Output()
    display(out_username)
    user = os.environ["username"]
    out_username.append_display_data(HTML(f"<p style='font-size: 20px; margin: 0;'>Logged in as: <strong>{user}</strong></p>"))

    out = Output()
    display(out)

    s = Stream(cache={}, debounce_sec=0.5)
    s.register(1, widgets=[lambda s: HTML("")], title="")
    s.register(2, updater=select_scenario, title="1) Select scenario (scenarios without results are ignored)", vertical=True, stacktrace_out=out)
    s.register(3, updater=select_result, title="2) Select execution result")
    s.register(4, updater=filter_widgets, title="3) Filter results (Keep CTRL pressed to select multiple)")
    s.register(5, updater=display_results_loading, vertical=True, title="4) Display results")
    s.display_registered()

    def update_stream():
        with_stacktrace(lambda: headers(s.cache, base_url), out)
        s.manually_update_stream(level_i_only=0)
    on_browser_ready(update_stream)

# LEVEL 1
def select_scenario(w: WidgetCurrentsChildren):
    project_id = os.environ["project_id"]
    cache = w.cache
    h = cache.get("h")
    if not h:
        loading = "Loading ..."
        w.display_or_update(RadioButtons(options=[(loading, loading)], layout={"width": "max-content"}))
        return

    utils_log.log("select_scenario()")
    scenario_display_to_guids = {}
    analyses = r.get(f"{be}projects/{project_id}", headers=h).json()["data"]["analyses"]

    # Collect all scenarios with their metadata
    scenarios_to_check = []
    for a in analyses:
        a_name = a["analysisName"]
        a_guid = a["analysisGuid"]
        for s in a["scenarios"]:
            s_name = s["scenarioName"]
            s_guid = s["scenarioGuid"]
            option = f"Analys: {a_name} | Scenar: {s_name}"
            scenarios_to_check.append((option, a_guid, a_name, s_guid, s_name))

    # Parallelize get_done_jobs() calls
    def check_scenario(scenario_data):
        option, a_guid, a_name, s_guid, s_name = scenario_data
        done_jobs = get_done_jobs(s_guid, base_url, h)
        if done_jobs:
            return (option, (a_guid, a_name, s_guid, s_name))
        return None

    utils_log.log("ThreadPoolExecutor - START")
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(check_scenario, scenarios_to_check)
    utils_log.log("ThreadPoolExecutor - DONE")

    # Filter out None results and build the dictionary
    for result in results:
        if result:
            option, guid_data = result
            scenario_display_to_guids[option] = guid_data

    cache["scenario_display_to_guids"] = scenario_display_to_guids
    options = list(scenario_display_to_guids)
    options = [(x, x) for x in options]
    options.sort()

    w.display_or_update(RadioButtons(options=options, layout={"width": "max-content"}))


def get_done_jobs(scenario_id, base_url, h):
    data = {"scenarioGuids": [scenario_id], "limit": 200}
    resp = r.post(f"{base_url}sense-api/ext/solver/jobs/get-scenarios", headers=h, json=data).json()
    return list(filter(lambda j: j["scenarioGuid"] == scenario_id and j["status"] == "DONE", resp))


# LEVEL 2
def select_result(w: WidgetCurrentsChildren):
    cache = w.cache
    h = cache.get("h")
    if not h:
        w.display_or_update(RadioButtons(layout={"width": "max-content"}, options=[]))
        return

    dropdown = w.parents[0]
    selected = dropdown.value
    (analysis_id, analysis_name, scenario_id, scenario_name) = cache["scenario_display_to_guids"][selected]
    done_jobs = get_done_jobs(scenario_id, base_url, h)
    started_to_job_id = {f'{x["started"]} | {x["name"]}': x["id"] for x in done_jobs}

    cache["started_to_job_id"] = started_to_job_id
    cache["analysis_id"] = analysis_id
    cache["analysis_name"] = analysis_name
    cache["scenario_id"] = scenario_id
    cache["scenario_name"] = scenario_name

    opts = [(x, x) for x in started_to_job_id]

    # update widget
    w.display_or_update(RadioButtons(layout={"width": "max-content"}, options=opts, value=opts[0][0]))


# LEVEL 3
filter_cols = ["Solution", "Stage", "Type", "Hub or Link"]

def select_multi_widget(desc, opts):
    if desc == "Hub or Link":
        desc = "Hub"

    value = opts
    if desc == "Solution" and opts:
        value = [opts[0]]

    return widgets.SelectMultiple(description=desc, options=opts, value=value)


def build_col_to_widget(widgets):
    col_to_widget = {}
    for i, col in enumerate(filter_cols):
        col_to_widget[col] = widgets[i]
    return col_to_widget


def filter_widgets(w: WidgetCurrentsChildren):
    col_to_opts = {}
    for col in filter_cols:
        col_to_opts[col] = []
    opts_net = []

    cache = w.cache
    if "h" in cache:
        dropdown_job = w.parents[0]
        build_df(dropdown_job, w.cache)
        df = cache["df"]
        df_copy = df.copy()
        df_copy.drop(df_copy[df_copy["Type"] == "Network"].index, inplace=True)

        for col in filter_cols:
            opts = list(set(df_copy[col]))
            opts.sort()
            col_to_opts[col] = opts

        # w_network_tech
        df_net = cache["df_network"]
        opts_net = sorted(df_net["Name"].unique().tolist())

    # update widgets
    for col, opts in col_to_opts.items():
        widg = select_multi_widget(col, opts)
        w.display_or_update(widg)

    widg_net = select_multi_widget("Network tech", opts_net)
    w.display_or_update(widg_net)


def apply_filter(col_to_widget, df):
    if df.empty:
        return df

    col_to_filter = {k: list(v.value) for k, v in col_to_widget.items() if v.value}
    for col, filt in col_to_filter.items():
        df = df[df[col].map(lambda x, f=filt: x in f)]

    return df


def remove_virtual(df):
    col = "Lifetime (years)"
    return df[df[col] != 1]

# Global file lock for all Voila kernels
def download_results(scenario, analysis, execution_name, job_id, headers, cache):
    lock_file = f"results_repository/{analysis}/{scenario}/{job_id}.lock"
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)

    with FileLock(lock_file):
        response = r.get(f"https://eu-north-1-api.sympheny.com/sense-api/ext/solver/jobs/{job_id}", headers=headers)
        job_name = response.json()["name"]

        input_dir = f"results_repository/{analysis}/{scenario}"
        input_path = f"{input_dir}/{execution_name}-{scenario}.xlsx"
        result_dir = f"results_repository/{analysis}/{scenario}/{job_name}/{job_id}"
        result_path = f"{result_dir}/{scenario}.zip"

        if not os.path.exists(input_dir):
            os.makedirs(input_dir)

        if not os.path.exists(result_dir):
            os.makedirs(result_dir)
        elif skip_download_results:
            return result_dir

        input_file = response.json()["inputFile"]
        output_file = response.json()["outputFile"]

        response = r.get(input_file, stream=True)
        with open(input_path, "wb") as f:
            f.write(response.content)

        response = r.get(output_file, stream=True)
        with open(result_path, "wb") as f:
            f.write(response.content)

        try:
            with zipfile.ZipFile(result_path, "r") as zip_ref:
                zip_ref.extractall(result_dir)
        except Exception as e:
            print("Failed unzip results: ", e)

        return result_dir


def build_df(dropdown_job, cache):
    h = cache.get("h")
    if not h:
        return

    started_to_job_id = cache["started_to_job_id"]
    analysis_name = cache["analysis_name"]
    scenario_name = cache["scenario_name"]
    selected_job = dropdown_job.value
    job_id = started_to_job_id[selected_job]
    cache["job_id"] = job_id
    result_dir = download_results(scenario_name, analysis_name, execution_name, job_id, h, cache)
    cache["result_dir"] = result_dir

    df, df_network = read_excel(result_dir)
    cache["df"] = df
    cache["df_network"] = df_network


def read_excel(result_dir):
    excel_name = [x for x in os.listdir(result_dir) if x.startswith("Summary")][0]
    excel = f"{result_dir}/{excel_name}"
    dfs = pd.read_excel(excel, sheet_name=["Cost & CO2", "Networks"])
    return dfs["Cost & CO2"], dfs["Networks"]


# LEVEL 4a
def display_results_loading(w: WidgetCurrentsChildren):
    # show spinner
    spinner_html = get_spinner_html()
    spinner_html.value = spinner_template.format(vis="inline-block", label="", t_str="")
    handle = w.display_or_update(widgets.VBox([spinner_html], layout=widgets.Layout(width="90%")))

    # build map
    try:
        display_results(w)

        # hide spinner
        spinner_result = spinner_template.format(vis="none", label="", t_str="")
    except Exception:
        spinner_result = stacktrace_html().value

    spinner_html.value = spinner_result
    handle.update(widgets.VBox([spinner_html], layout=widgets.Layout(width="90%")))

def display_results(w: WidgetCurrentsChildren):
    def display_empty():
        # sankey
        w.sub_title("")
        w.display_or_update(HTML())

        # datagrid
        w.sub_title("")
        w.display_or_update(VBox([]))
        w.display_or_update(HTML())

        # datagrid aggregate
        w.sub_title("")
        w.display_or_update(VBox([]))
        w.display_or_update(HTML())

        # chart pie
        w.display_or_update(HTML())
        w.display_or_update(HTML())
        w.display_or_update(HTML())

        # download
        chart_download(w)

        # map
        w.sub_title("")
        w.display_or_update(HTML())
        w.display_or_update(VBox([]))

    if not "h" in w.cache:
        display_empty()
        return

    # sankey
    w.sub_title("SANKEY URL (using first value of Solution, Stage and Hub dropdowns)")
    sankey_url(w)

    # datagrid
    df_table(w)

    # datagrid aggregate
    w.sub_title("Aggregate on (Hub)")
    df_table_agg(w)

    # chart pie
    chart_pie(w)
    chart_bar_vert(w)
    chart_bar(w)
    chart_download(w)

    # map
    w.sub_title("Network map (only sensitive to Solution, Stage and Network filters)")
    handle = w.display_or_update(HTML(""))
    display_NETWORK(w, handle)


def format_thousands(x):
    if isinstance(x, float) and not numpy.isnan(x):
        x = round(x)
        apostrophe = "\u0027"
        return f"{x:,}".replace(",", apostrophe)

    return x


def sankey_url(w: WidgetCurrentsChildren):
    cache = w.cache
    col_to_widget = build_col_to_widget(w.parents)

    if not col_to_widget["Solution"].value or not col_to_widget["Stage"].value or not col_to_widget["Hub or Link"].value:
        sankey_url = "SELECT AT LEAST 1 Solution, 1 Stage and 1 Hub"
    else:
        point = col_to_widget["Solution"].value[0].split(" ")[1]
        stage = col_to_widget["Stage"].value[0]
        hub = col_to_widget["Hub or Link"].value[0]

        project_id = os.environ["project_id"]
        analysis_id = cache["analysis_id"]
        job_id = cache["job_id"]

        sankey_url = (
            f"https://app.sympheny.com/projects/{project_id}/analysis/{analysis_id}"
            f"/execution/{job_id}/solution/{point}/general?stage={stage}&hub={hub}"
        )
        sankey_url = sankey_url.replace(" ", "%20")
        sankey_url = f'<a style="color: blue;" target="_blank" rel="noopener noreferrer" href="{sankey_url}">{sankey_url}</a>'

    handle = w.display_or_update(HTML(sankey_url))
    handle.existing.value = sankey_url

def df_table(w: WidgetCurrentsChildren):
    df = w.cache["df"]
    col_to_widget = build_col_to_widget(w.parents)

    df_filt = df.copy()
    df_filt = apply_filter(col_to_widget, df_filt)
    df_filt = df_filt.map(format_thousands)
    df_filt = df_filt.dropna(axis=1, how="all")

    # update widget
    grid = DataGrid(
        df_filt,
        selection_mode="cell",
        base_column_size=200,
        base_row_header_size=300,
        layout={"height": "250px"},
    )
    grid.auto_fit_columns = True

    w.sub_title("Results table")
    w.display_or_update(widgets.VBox([grid]))
    w.display_or_update(download_button(df_filt))

# AGGREGATE
def df_table_agg(w: WidgetCurrentsChildren):
    col_aggrs = ["Hub or Link"]
    col_drops = ["Solution", "Stage", "Type", "Name", "Hub or Link"]

    cache = w.cache
    df = cache["df"]
    col_to_widget = build_col_to_widget(w.parents)

    df_filt = df.copy()
    df_filt = apply_filter(col_to_widget, df_filt)

    df_filt = df_table_agg_filt(df_filt, col_aggrs, col_drops)

    grid = DataGrid(
        df_filt,
        selection_mode="cell",
        base_column_size=200,
        base_row_header_size=300,
        layout={"height": "250px"},
    )
    grid.auto_fit_columns = True

    w.display_or_update(widgets.VBox([grid]))
    w.display_or_update(download_button(df_filt))


def df_table_agg_filt(df_filt, col_aggrs, col_drops):
    col_drops = [x for x in col_drops if x in df_filt.columns and x not in col_aggrs]
    df_filt = df_filt.drop(columns=col_drops)

    df_filt = df_filt.dropna(axis=1, how="all")

    if not set(col_aggrs).issubset(df_filt.columns):
        return pd.DataFrame({col_aggrs[0]: []})

    df_filt = df_filt.groupby(col_aggrs, as_index=False).sum()
    df_filt = df_filt.map(format_thousands)
    return df_filt


# LEVEL 4b
def chart_pie(w: WidgetCurrentsChildren):
    cache = w.cache
    df = cache["df"]
    col_to_widget = build_col_to_widget(w.parents)

    df_filt = df.copy()
    df_filt = df_filt[df_filt[col_value] > 0]
    df_filt = apply_filter(col_to_widget, df_filt)
    df_filt = remove_virtual(df_filt)

    df_filt = df_filt[df_filt.columns.intersection([col_name, col_value])]
    df_filt = df_filt.groupby(col_name, as_index=False).sum()

    # draw
    names = df_filt[col_name]
    values = [round(x, 2) for x in df_filt[col_value]]
    legend_labels = [f"{format_thousands(x)} k" for x in values]

    # pie chart
    fig1 = go.Figure(data=[go.Pie(labels=names, values=values, textinfo="text+percent", hoverinfo="label", text=legend_labels)])
    fig1.update_traces(textposition="inside")
    fig1.update_layout(title=col_value, legend_title=col_name, width=800, height=800, uniformtext_minsize=12, uniformtext_mode="hide")
    w.display_or_update(plotly_fig_to_html(fig1))


# LEVEL 4c
def chart_bar_vert(w: WidgetCurrentsChildren):
    cache = w.cache
    df = cache["df"]
    col_to_widget = build_col_to_widget(w.parents)

    df_filt = df.copy()
    df_filt = df_filt[df_filt[col_value] > 0]
    df_filt = apply_filter(col_to_widget, df_filt)
    df_filt = remove_virtual(df_filt)

    df_filt = df_filt[df_filt.columns.intersection([col_name, col_value])]
    df_filt = df_filt.groupby(col_name, as_index=False).sum()
    names = df_filt[col_name]
    values = [round(x, 2) for x in df_filt[col_value]]
    name_values = list(zip(names, values))
    name_values = list(sorted(name_values, key=lambda x: x[1], reverse=True))

    # name_values = name_values[:20]
    names = [n for n, _ in name_values]
    values = [v for _, v in name_values]

    values = [round(v, 2) for v in values]
    data = {"name": names, "value": values}
    data = pd.DataFrame.from_dict(data)

    fig = px.bar(data, x="name", y="value", text="value")
    fig.update_traces(textposition="none")
    fig.update_layout(xaxis_title="", yaxis_title=col_value, legend_traceorder="normal")

    for i, _ in enumerate(fig.data):
        fig.data[i]["hovertemplate"] = "%{y:,.0f}"

    w.display_or_update(plotly_fig_to_html(fig))


# LEVEL 4d
def chart_bar(w: WidgetCurrentsChildren):
    cache = w.cache
    df = cache["df"]
    col_to_widget = build_col_to_widget(w.parents)

    df_filt = df.copy()
    df_filt = df_filt[df_filt[col_value] > 0]
    df_filt = apply_filter(col_to_widget, df_filt)
    df_filt = remove_virtual(df_filt)

    df_filt = df_filt[df_filt.columns.intersection([col_name, col_value])]
    df_filt = df_filt.groupby(col_name, as_index=False).sum()
    names = df_filt[col_name]
    values = [round(x, 2) for x in df_filt[col_value]]
    name_values = list(zip(names, values))
    name_values = list(sorted(name_values, key=lambda x: x[1], reverse=True))
    total = sum([v for _, v in name_values])

    # name_values = name_values[:20]
    names = [n for n, _ in name_values]
    values = [v for _, v in name_values]
    y = [0] * len(names)
    labels = [f"{round(v*100/total, 2)} %" for v in values]
    data = {"name": names, "value": values, "label": labels, "y": y}
    data = pd.DataFrame.from_dict(data)

    fig = px.bar(data, x="value", y="y", color="name", labels="name", text="label", orientation="h")
    fig.update_yaxes(visible=False, showticklabels=False)
    fig.update_traces(textposition="inside")
    fig.update_layout(barmode="stack", legend_traceorder="normal", uniformtext_minsize=12, uniformtext_mode="hide", xaxis_title=col_value)

    for i, trace in enumerate(fig.data):
        fig.data[i]["hovertemplate"] = trace.name

    fig_widget = plotly_fig_to_html(fig)
    fig_html = fig_widget.value
    os.makedirs("iframe_figures", exist_ok=True)
    files = glob.glob("iframe_figures/bar*.html")
    files.sort()
    for file_to_remove in files[:-10]:  # Remove all except the last 10
        try:
            os.remove(file_to_remove)
        except Exception:
            pass

    with open(bar_path, "w", encoding="utf-8") as f:
        f.write(fig_html)

    w.display_or_update(fig_widget)

# LEVEL 4e
def chart_download(w: WidgetCurrentsChildren):
    download_html = widgets.VBox([HTML("")])
    w.display_or_update(download_html)

    def on_done():
        download_html.children = [HTML("")]

    def chart():
        download_html.children = [HTML("<font size='3' color='red'>Downloading, please wait ...</font>")]

        with open(bar_path, "rb") as f:
            res = f.read()
            threading.Timer(1, on_done).start()
            return res

    dl = solara.FileDownload(chart, filename="bar.html", label="Download chart")
    w.display_or_update(dl)

def init_map():
    m = Map()
    m.layout.width = "100%"
    m.layout.height = "500px"
    return m

# LEVEL 4f
def display_NETWORK(w: WidgetCurrentsChildren, handle: Handle):
    cache = w.cache
    handle.existing.value = hovered_network_text(None, None)

    m = init_map()
    button = Button(button_style="danger", description="Loading....", disabled=True)
    w.display_or_update(widgets.VBox([m, button]))

    # build map-
    utils_log.log("build_map_NETWORK() - START")
    build_map_NETWORK(w, handle, m)
    utils_log.log("build_map_NETWORK() - DONE")

    # re-enable center map button
    button.description = "Center map"
    button.disabled = False

    def on_zoom_click(b):
        m.center = cache["center"]
        m.zoom = cache["zoom"]
    button.on_click(on_zoom_click)

def build_map_NETWORK(w: WidgetCurrentsChildren, handle: Handle, m):
    network_widg = w.parents[-1]
    handle.existing.value = hovered_network_text(None, None)

    cache = w.cache
    df_net = cache["df_network"]

    # filter dataframe
    col_to_widget = build_col_to_widget(w.parents)
    col_to_widget = {k: v for k, v in col_to_widget.items() if k in filters_that_apply}

    network_names = list(network_widg.value)
    col_to_widget["Name"] = network_widg
    df_net = apply_filter(col_to_widget, df_net)

    # aggregate duplicates
    df_net = df_net.groupby(["Link"], as_index=False).sum()

    # get links
    networks = [(row["Link"], row[col_value_net]) for _, row in df_net.iterrows()]

    h = cache["h"]
    scenario_id = cache["scenario_id"]

    network_layers = r.get(f"{base_url}api-services/gis/scenarios/{scenario_id}/networks", headers=h).json()
    link_id_to_geojson = {x["link_id"]: {"features": [x["feature"]]} for x in network_layers}
    scenario_links = r.get(f"{be}v2/scenarios/{scenario_id}/network-links", headers=h).json()["data"]
    link_id_to_name = {x["networkLinkGuid"]: x["name"] for x in scenario_links}
    link_name_to_id = {v: k for k, v in link_id_to_name.items()}
    link_ids = [x for x in link_id_to_name.keys() if x in link_id_to_geojson]
    link_to_geojson = {link_id_to_name[id]: link_id_to_geojson[id] for id in link_ids}

    network_geojsons = []
    missing_link_geojson = []
    link_id_to_value = {}
    for net in networks:
        link_name = net[0]
        link_value = net[1]
        if link_name not in link_to_geojson:
            missing_link_geojson.append(link_name)
            continue

        geojson = link_to_geojson[link_name]
        network_geojsons.append((link_value, geojson, link_name))

        link_id = link_name_to_id[link_name]
        link_id_to_value[link_id] = link_value

    # build map
    max_value = max([x[0] for x in network_geojsons]) if network_geojsons else 0
    m.layout.width = "100%"
    m.layout.height = "500px"

    # legend = {f"Layer {building_layer}": "green", "hubs": "blue"}
    legend = {"hubs": "blue"}

    for i, net in enumerate(network_names):
        legend[net] = color_palette[i % len(color_palette)]

    m.add(
        LegendControl(
            legend,
            name=f"Thickest network: {format_thousands(max_value)} kW",
            position="topright",
        )
    )

    # add building layer 'Plan_guide_V2 - adapted'
    layers = r.get(f"{base_url}api-services/gis/scenarios/{scenario_id}/layers-presigned", headers=h).json()
    layers_filtered = [x["layer_id"] for x in layers if x["layer_name"] == building_layer]

    if layers_filtered:
        layer_id = layers_filtered[0]
        layer_url = r.get(f"{base_url}api-services/gis/scenarios/{scenario_id}/layers-presigned/{layer_id}", headers=h).json()["url"]
        geojson = json.loads(r.get(layer_url).content.decode("utf-8"))["feature_collection"]
        m.add(GeoJSON(data=geojson, style={"color": "green", "weight": 1}))

    # add hubs
    resp = r.get(f"{be}scenarios/{scenario_id}/hubs", headers=h).json()["data"]
    hub_ids = [x["hubGuid"] for x in resp]

    utils_log.log(f"hub_ids size: {len(hub_ids)}")
    def fetch_and_add_hub(hub_id):
        resp = r.get(f"{base_url}api-services/gis/scenarios/{scenario_id}/hubs/{hub_id}", headers=h).json()
        if not resp:
            return

        geojson_base = resp["base_layer"]
        m.add(GeoJSON(data=geojson_base, style={"color": "deepskyblue", "weight": 2}))

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(fetch_and_add_hub, hub_ids)

    # add networks
    def on_hover_network(*args, **kwargs):
        link_id = kwargs["feature"]["properties"]["link_id"]
        name = link_id_to_name[link_id]
        value = link_id_to_value[link_id]
        handle.existing.value = hovered_network_text(name, value)

    features = []
    for value, geojson, _ in network_geojsons:
        features.append(geojson["features"][0])

        if max_value > 0:
            weight = 15 * value / max_value
            hover_style = {"color": hovered_net_color, "dashArray": "0", "fillOpacity": 0.5}
            name = geojson["features"][0]["properties"]["network_name"]
            feat = GeoJSON(data=geojson, style={"color": legend[name], "weight": weight}, hover_style=hover_style)
            feat.on_hover(on_hover_network)
            m.add(feat)

    # cache["bounds"] = get_bounds({"features": features})
    base_layer = {"features": features}

    compute_center_zoom(base_layer, cache)
    if cache["center"]:
        m.center = cache["center"]
        m.zoom = cache["zoom"]


def compute_center_zoom(base_layer, cache):
    """
    Compute center point and appropriate zoom level for GeoJSON layer.
    Uses the same coordinate extraction logic as get_bounds().
    """
    minX, minY, maxX, maxY = None, None, None, None
    all_lats, all_lons = [], []

    for ft in base_layer["features"]:
        coords = ft["geometry"]["coordinates"][0][0]
        if not isinstance(coords, list):
            coords = ft["geometry"]["coordinates"]

        for c in coords:
            x, y = c[0], c[1]
            all_lons.append(x)
            all_lats.append(y)

            if minX is None:
                minX = x
                maxX = x
                minY = y
                maxY = y
            else:
                if x < minX:
                    minX = x
                if x > maxX:
                    maxX = x
                if y < minY:
                    minY = y
                if y > maxY:
                    maxY = y

    # Compute center (using average of all coordinates)
    if not all_lats or not all_lons:
        cache["center"] = None
        cache["zoom"] = None
        cache["bounds"] = None
        return

    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)
    center = (center_lat, center_lon)

    # Compute bounds
    bounds = [[minY, minX], [maxY, maxX]]

    # Compute zoom dynamically based on polygon size
    lat_diff = maxY - minY
    lon_diff = maxX - minX
    max_diff = max(lat_diff, lon_diff)

    # Calculate zoom level (rough approximation)
    if max_diff > 0:
        padding_factor = 1.5
        adjusted_diff = max_diff * padding_factor
        zoom = int(math.log2(360 / adjusted_diff)) + 1
        zoom = max(1, min(zoom, 20))
    else:
        zoom = 18

    zoom = 16
    utils_log.log(f"Map zoom {zoom}")

    cache["center"] = center
    cache["zoom"] = zoom
    cache["bounds"] = bounds

def hovered_network_text(name, value):
    if not name or not value:
        name = "_"
        value = "_"
    else:
        value = format_thousands(value)

    text = (
        f"<font color='black' size=5 style='font-weight: normal'>Hovered network name: </>"
        f"<font size=5 color='{hovered_net_color}' style='font-weight: bold'>{name}</>"
        f"<br></><font color='black' size=5 style='font-weight: normal'>Hovered network value: </>"
        f"<font size=5 color='{hovered_net_color}' style='font-weight: bold'>{value} kW</>"
    )
    return text