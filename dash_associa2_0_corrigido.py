import re
import io
from pathlib import Path
import unicodedata

import pandas as pd
import requests
from dash import Dash, dcc, html, Input, Output, State, dash_table
import plotly.express as px
import plotly.graph_objects as go


# =========================================================
# CONFIGURAÇÃO DA FONTE DE DADOS
# =========================================================
# Pode ser:
# - caminho local CSV/XLSX
# - link Google Drive "view"
# - link direto de download
# - Google Sheets CSV publicado
DATA_SOURCE = "https://docs.google.com/spreadsheets/d/1BQvUkVC9hAQIWgVHV42_JsQwlSVhrGYe/export?format=csv"

# link do formulário
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdZJ3ARpU9ej_xyanT2wfWyotBC_WMY_jsZhRgRRXmzuLylew/viewform"

# paleta roxa institucional
PURPLE_SCALE = [
    [0.0,  "#F3EEFF"],
    [0.2,  "#DDD0FF"],
    [0.4,  "#C2ACFF"],
    [0.6,  "#9B77F2"],
    [0.8,  "#7C4DDB"],
    [1.0,  "#5B21B6"],
]


# =========================================================
# HELPERS VISUAIS
# =========================================================
def apply_plot_theme(fig, title_color="#3B0764", text_color="#1F1B2E",
                     paper_bg="#FBF9FF", plot_bg="#FBF9FF"):
    fig.update_layout(
        font={"family": "Arial, Roboto, sans-serif", "color": text_color},
        title={
            "font": {"color": title_color, "size": 20},
            "x": 0.5,
            "xanchor": "center"
        },
        margin={"l": 10, "r": 10, "t": 60, "b": 10},
        paper_bgcolor=paper_bg,
        plot_bgcolor=plot_bg,
        coloraxis_colorbar={
            "tickfont": {"color": text_color},
            "title": {"font": {"color": title_color}}
        },
    )
    return fig


def blank_fig(msg: str):
    fig = go.Figure()
    fig.update_layout(
        title=msg,
        xaxis={"visible": False},
        yaxis={"visible": False},
        paper_bgcolor="#D3D3D3",
        plot_bgcolor="#D3D3D3",
        annotations=[{
            "text": msg,
            "xref": "paper",
            "yref": "paper",
            "showarrow": False,
            "font": {"size": 14, "color": "#1F1B2E"}
        }]
    )
    return fig



def apply_map_background(fig, geo_bg="#D3D3D3"):
    fig.update_geos(
        showland=True,
        landcolor="#F7F4FC",
        showocean=True,
        oceancolor=geo_bg,
        showlakes=True,
        lakecolor=geo_bg,
        showcountries=False,
        showcoastlines=False,
        showframe=False,
        bgcolor=geo_bg,
        subunitcolor="rgba(91,33,182,0.42)",
        subunitwidth=1.2,
    )
    fig.update_layout(
        geo=dict(bgcolor=geo_bg),
        paper_bgcolor=geo_bg,
        plot_bgcolor=geo_bg,
        hoverlabel={
            "bgcolor": "white",
            "font": {"color": "#1F1B2E"}
        },
        coloraxis_colorbar={
            "title": "Nº de associações",
            "thickness": 14,
            "len": 0.75
        }
    )
    return fig


# =========================================================
# HELPERS GERAIS
# =========================================================
def safe_col(d: pd.DataFrame, col: str) -> pd.Series:
    if col in d.columns:
        return d[col]
    return pd.Series([None] * len(d), index=d.index)


def as_stripped(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip()


def normalize_colname(c: str) -> str:
    c = str(c)
    c = c.replace("\n", " ").replace("\r", " ").strip()
    c = re.sub(r"\s+", " ", c)
    c = c.strip('"').strip()
    return c


def convert_drive_url(url: str) -> str:
    if not isinstance(url, str):
        return url

    if "drive.google.com/file/d/" in url:
        file_id = url.split("/d/")[1].split("/")[0]
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    return url


def is_url(path_or_url: str) -> bool:
    return isinstance(path_or_url, str) and (
        path_or_url.startswith("http://") or path_or_url.startswith("https://")
    )


def _try_read_excel(content: bytes) -> pd.DataFrame | None:
    try:
        return pd.read_excel(io.BytesIO(content))
    except Exception:
        return None


def _try_read_csv_bytes(content: bytes, sep: str, encoding: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(
            io.BytesIO(content),
            sep=sep,
            encoding=encoding,
            engine="python",
            on_bad_lines="skip"
        )
        if df is not None and (df.shape[1] > 1 or len(df) > 0):
            return df
    except Exception:
        return None
    return None


def _try_read_csv_path(path: Path, sep: str, encoding: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(
            path,
            sep=sep,
            encoding=encoding,
            engine="python",
            on_bad_lines="skip"
        )
        if df is not None and (df.shape[1] > 1 or len(df) > 0):
            return df
    except Exception:
        return None
    return None


def read_bytes_as_table(content: bytes, source_name: str = "") -> pd.DataFrame:
    lower_name = source_name.lower()

    # Se a origem indica Excel, tenta Excel primeiro.
    if lower_name.endswith((".xlsx", ".xls", "format=xlsx", "format=xls")):
        df_excel = _try_read_excel(content)
        if df_excel is not None:
            return df_excel

    # Mesmo sem extensão confiável, ainda tenta Excel antes de CSV.
    df_excel = _try_read_excel(content)
    if df_excel is not None:
        return df_excel

    # CSV com combinações robustas de encoding e separador.
    encodings = ["utf-8", "utf-8-sig", "latin1", "cp1252", "iso-8859-1"]
    seps = [",", ";", "\t", "|"]

    for encoding in encodings:
        for sep in seps:
            df_csv = _try_read_csv_bytes(content, sep=sep, encoding=encoding)
            if df_csv is not None:
                return df_csv

    # Último fallback: decodifica ignorando caracteres inválidos.
    text = content.decode("latin1", errors="replace")
    for sep in seps:
        try:
            df = pd.read_csv(
                io.StringIO(text),
                sep=sep,
                engine="python",
                on_bad_lines="skip"
            )
            if df.shape[1] > 1 or len(df) > 0:
                return df
        except Exception:
            pass

    raise ValueError(f"Não foi possível ler a fonte de dados: {source_name}")


def load_table(source: str) -> pd.DataFrame:
    source = convert_drive_url(source)

    if is_url(source):
        r = requests.get(source, timeout=60)
        r.raise_for_status()
        return read_bytes_as_table(r.content, source)

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {source}")

    suffix = path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(path)

    if suffix == ".csv":
        encodings = ["utf-8", "utf-8-sig", "latin1", "cp1252", "iso-8859-1"]
        seps = [",", ";", "\t", "|"]
        for encoding in encodings:
            for sep in seps:
                df = _try_read_csv_path(path, sep=sep, encoding=encoding)
                if df is not None:
                    return df

    with open(path, "rb") as f:
        return read_bytes_as_table(f.read(), str(path))


def uniq_sorted(series):
    vals = series.dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    return sorted(vals.unique().tolist())


def norm_resp(x):
    if pd.isna(x):
        return "NI"
    s = str(x).strip().lower()
    s = s.replace(".", "").replace(";", "").replace(",", "")
    if s in ["sim", "s", "yes", "y"]:
        return "sim"
    if s in ["não", "nao", "n", "no"]:
        return "não"
    if s in ["ni", "n/i", "na", "n a", "não informado", "nao informado"]:
        return "NI"
    if s in ["mp", "m/p"]:
        return "MP"
    return str(x).strip()


def is_found_link(x):
    if pd.isna(x):
        return False
    s = str(x).strip().lower()
    if "não encontrado" in s or "nao encontrado" in s:
        return False
    return s.startswith("http")


def is_checked_service_value(x):
    if pd.isna(x):
        return False
    s = str(x).strip().lower()
    if not s or s in ["nan", "none", "ni", "n/i", "na", "n a", "não informado", "nao informado"]:
        return False
    return s in ["sim", "s", "yes", "y", "não", "nao", "n", "no", "mp", "m/p"]


def compute_verificada_flag(row: pd.Series, service_cols: list[str]) -> bool:
    available = [c for c in service_cols if c in row.index]
    if not available:
        return False
    return any(is_checked_service_value(row[c]) for c in available)


def parse_mixed_date(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    parsed = pd.to_datetime(series, errors="coerce")
    mask = parsed.isna() & series.notna()
    if mask.any():
        parsed.loc[mask] = pd.to_datetime(series.loc[mask], errors="coerce", dayfirst=True)
    return parsed


def shorten_service_label(label: str) -> str:
    mapping = {
        "Distribuição de óleo à base de cannabis (sim, não ou NI)": "Distribuição de óleo",
        "Distribuição de pomada/gel/creme à base de cannabis (sim, não ou NI)": "Pomada / gel / creme",
        "Distribuição de produtos específicos para pets à base de cannabis (sim, não ou NI)": "Produtos para pets",
        "Possui algum outro produto para distribuição à base de cannabis? (sim, não ou NI)": "Outros produtos",
        "Oferece atendimento médico? (sim, não, NI ou MP)": "Atendimento médico",
        "Oferece assistência jurídica? (sim, não ou NI)": "Assistência jurídica",
        'Oferece "acolhimento"? (sim, não ou NI)': "Acolhimento",
        "Oferece algum outro tipo de serviço? (sim, não ou NI)": "Outro serviço",
    }
    return mapping.get(label, label)


def make_donut_from_counts(counts_df: pd.DataFrame, names_col: str, values_col: str, title: str):
    if counts_df.empty or counts_df[values_col].sum() == 0:
        return blank_fig(f"{title}: sem dados no recorte atual.")

    fig = px.pie(
        counts_df,
        names=names_col,
        values=values_col,
        hole=0.58,
        title=title,
        color_discrete_sequence=["#5B21B6", "#6D28D9", "#7C3AED", "#8B5CF6", "#C4B5FD", "#DDD6FE"],
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent+label",
        hovertemplate="%{label}: %{value}<extra></extra>"
    )
    fig.update_layout(
        paper_bgcolor="#FBF9FF",
        plot_bgcolor="#FBF9FF",
        legend_title_text="",
        margin={"l": 10, "r": 10, "t": 60, "b": 10},
    )
    return apply_plot_theme(fig)


# =========================================================
# NORMALIZAÇÃO DE MUNICÍPIO
# =========================================================
def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_mun_name(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip().upper()
    s = strip_accents(s)
    s = re.sub(r"\s+", " ", s)
    return s


def make_md_link(url, label: str) -> str:
    if pd.isna(url):
        return ""
    s = str(url).strip()
    if not s:
        return ""
    low = s.lower()
    if "não encontrado" in low or "nao encontrado" in low:
        return ""
    if not low.startswith("http"):
        return ""
    return f"[{label}]({s})"


# =========================================================
# GEOJSON MUNICIPAL POR UF
# =========================================================
GEO_MUN_CACHE = {}

UF_TO_GEOJS = {
    "AC":"12","AL":"27","AM":"13","AP":"16","BA":"29","CE":"23","DF":"53","ES":"32","GO":"52",
    "MA":"21","MG":"31","MS":"50","MT":"51","PA":"15","PB":"25","PE":"26","PI":"22","PR":"41",
    "RJ":"33","RN":"24","RO":"11","RR":"14","RS":"43","SC":"42","SE":"28","SP":"35","TO":"17"
}


def get_mun_geojson_for_uf(uf_sigla: str):
    uf_sigla = (uf_sigla or "").upper().strip()
    if uf_sigla in GEO_MUN_CACHE:
        return GEO_MUN_CACHE[uf_sigla]

    code = UF_TO_GEOJS.get(uf_sigla)
    if not code:
        return None

    url = f"https://raw.githubusercontent.com/tbrugz/geodata-br/master/geojson/geojs-{code}-mun.json"
    gj = requests.get(url, timeout=30).json()
    GEO_MUN_CACHE[uf_sigla] = gj
    return gj


def guess_prop_key(geojson: dict):
    try:
        props = geojson["features"][0]["properties"]
        for k in ["name", "NM_MUN", "nome", "Name", "NAME"]:
            if k in props:
                return k
        for k, v in props.items():
            if isinstance(v, str):
                return k
    except Exception:
        pass
    return None


# =========================================================
# CARREGAMENTO DA BASE
# =========================================================
df_raw = load_table(DATA_SOURCE)
df = df_raw.copy()
df.columns = [normalize_colname(c) for c in df.columns]

for maybe_idx in ["Unnamed: 0", "unnamed: 0"]:
    if maybe_idx in df.columns:
        df = df.drop(columns=[maybe_idx])


# =========================================================
# COMPATIBILIDADE COM BASE NOVA
# =========================================================
COL_UF_LONGA = "Unidade da Federação (Estado ou Distrito Federal de localização a partir do endereço divulgado na página)"
COL_MUN_LONGA = "Município (de localização a partir do endereço divulgado na página)"
COL_NOME_LONGA = "Nome da associação (nome sem abreviaturas divulgado na página)"

if COL_UF_LONGA in df.columns:
    df["UF"] = df[COL_UF_LONGA].astype(str).str.strip()

if COL_MUN_LONGA in df.columns:
    df["Município"] = df[COL_MUN_LONGA].astype(str).str.strip()

if COL_NOME_LONGA in df.columns:
    df["Nome da associação"] = df[COL_NOME_LONGA].astype(str).str.strip()


# =========================================================
# COLUNAS PRINCIPAIS
# =========================================================
COL_ID = "ID"
COL_NOME = "Nome da associação"
COL_SIGLA = "Sigla ou Nome Comercial"
COL_UF = "UF"
COL_MUN = "Município"
COL_INSTAGRAM = 'Página do Instagram (link ou "não encontrado")'
COL_SITE = 'Site ou página web (link ou "não encontrado")'
COL_OBS = "Observação"
COL_CNPJ = "CNPJ"
COL_DT_FUND = "dt_fundacao_osc"

SERV_COLS = [
    "Distribuição de óleo à base de cannabis (sim, não ou NI)",
    "Distribuição de pomada/gel/creme à base de cannabis (sim, não ou NI)",
    "Distribuição de produtos específicos para pets à base de cannabis (sim, não ou NI)",
    "Possui algum outro produto para distribuição à base de cannabis? (sim, não ou NI)",
    "Oferece atendimento médico? (sim, não, NI ou MP)",
    "Oferece assistência jurídica? (sim, não ou NI)",
    'Oferece "acolhimento"? (sim, não ou NI)',
    "Oferece algum outro tipo de serviço? (sim, não ou NI)",
]

required_cols = [COL_ID, COL_NOME, COL_SIGLA, COL_UF, COL_MUN]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    raise ValueError(f"Colunas não encontradas na base: {missing}")


UF_SIGLA = {
    "Acre":"AC","Alagoas":"AL","Amapá":"AP","Amazonas":"AM","Bahia":"BA","Ceará":"CE",
    "Distrito Federal":"DF","Espírito Santo":"ES","Goiás":"GO","Maranhão":"MA",
    "Mato Grosso":"MT","Mato Grosso do Sul":"MS","Minas Gerais":"MG","Pará":"PA",
    "Paraíba":"PB","Paraná":"PR","Pernambuco":"PE","Piauí":"PI","Rio de Janeiro":"RJ",
    "Rio Grande do Norte":"RN","Rio Grande do Sul":"RS","Rondônia":"RO","Roraima":"RR",
    "Santa Catarina":"SC","São Paulo":"SP","Sergipe":"SE","Tocantins":"TO"
}

uf_raw = df[COL_UF].astype(str).str.strip()
uf_raw = uf_raw.replace({
    "": None,
    "NI": None,
    "ni": None,
    "Não informado": None,
    "não informado": None,
    "Nao informado": None,
    "nao informado": None,
    "nan": None,
    "None": None
})

df["uf_sigla"] = uf_raw.map(UF_SIGLA)

uf_up = uf_raw.astype(str).str.upper()
df.loc[uf_up.str.fullmatch(r"[A-Z]{2}", na=False), "uf_sigla"] = uf_up

df["associacao_verificada"] = df.apply(lambda row: compute_verificada_flag(row, SERV_COLS), axis=1)
df["status_verificacao"] = df["associacao_verificada"].map({True: "Verificada", False: "Não verificada"})

for c in SERV_COLS:
    if c in df.columns:
        df[c] = df[c].apply(norm_resp)

df["tem_instagram"] = df[COL_INSTAGRAM].apply(is_found_link) if COL_INSTAGRAM in df.columns else False
df["tem_site"] = df[COL_SITE].apply(is_found_link) if COL_SITE in df.columns else False
df["tem_cnpj"] = safe_col(df, COL_CNPJ).astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}).notna() if COL_CNPJ in df.columns else False
if COL_DT_FUND in df.columns:
    df["dt_fundacao_parsed"] = parse_mixed_date(df[COL_DT_FUND])
    df["ano_fundacao"] = df["dt_fundacao_parsed"].dt.year
else:
    df["dt_fundacao_parsed"] = pd.NaT
    df["ano_fundacao"] = pd.NA

print("\n=== DIAGNÓSTICO INICIAL ===")
print(df[[COL_UF, COL_MUN, "uf_sigla"]].head(10).to_string())


# =========================================================
# GEOJSON UF
# =========================================================
BRAZIL_STATES_GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "codeforamerica/click_that_hood/master/"
    "public/data/brazil-states.geojson"
)
BRAZIL_STATES_GEOJSON = requests.get(BRAZIL_STATES_GEOJSON_URL, timeout=30).json()


# =========================================================
# APP
# =========================================================
app = Dash(__name__)
app.title = "Dashboard — Associações (modelo tipo Ipea)"

uf_opts = uniq_sorted(df[COL_UF]) if COL_UF in df.columns else []
mun_opts = uniq_sorted(df[COL_MUN]) if COL_MUN in df.columns else []

LISTA_STYLE_TABLE = {"overflowX": "auto"}
LISTA_STYLE_CELL = {"textAlign": "left", "whiteSpace": "normal", "height": "auto", "minWidth": "120px", "maxWidth": "420px"}
LISTA_STYLE_HEADER = {"fontWeight": "bold"}

HEADER_H = "96px"

app.layout = html.Div([

    html.Div([
        html.Div([
            html.Div([
                html.H1("Associações Cannábicas — Painel", className="brand-title"),
                html.P("Fontes: dados Psicocult, Fiocruz e INCT-InEAC", className="brand-subtitle")
            ], className="brand-text"),
            html.Div([
                html.Img(src="/assets/logo_psicocult.png", className="brand-logo psicocult-logo"),
                html.Img(src="/assets/marcafiocruz_horizontal_POSITIVA_24052024-scaled-1.jpg", className="brand-logo fiocruz-logo"),
                html.Img(src="/assets/unnamed.jpg", className="brand-logo inct-logo"),
            ], className="brand-logos")
        ], className="app-header-inner", style={
            "display": "flex",
            "justifyContent": "space-between",
            "alignItems": "center",
            "gap": "20px",
            "flexWrap": "wrap"
        })
    ], className="app-header"),

    html.Div([

        html.Div([

            html.Div([
                html.H4("Filtros"),

                html.Label("UF"),
                dcc.Dropdown(id="f-uf", options=[{"label": u, "value": u} for u in uf_opts], multi=True),

                html.Br(),
                html.Label("Município"),
                dcc.Dropdown(id="f-mun", options=[{"label": m, "value": m} for m in mun_opts], multi=True),

                html.Br(),
                html.Label("Status de verificação"),
                dcc.Dropdown(
                    id="f-verificacao",
                    options=[
                        {"label": "Todas", "value": "todas"},
                        {"label": "Apenas verificadas", "value": "verificadas"},
                        {"label": "Apenas não verificadas", "value": "nao_verificadas"},
                    ],
                    value="todas",
                    clearable=False
                ),

                html.Br(),
                html.Label("CNPJ"),
                dcc.Dropdown(
                    id="f-cnpj",
                    options=[
                        {"label": "Todas", "value": "todas"},
                        {"label": "Com CNPJ", "value": "com_cnpj"},
                        {"label": "Sem CNPJ", "value": "sem_cnpj"},
                    ],
                    value="todas",
                    clearable=False
                ),

                html.Hr(),
                html.H4("Serviços / Produtos (mostrar apenas quem tem)"),
                dcc.Checklist(
                    id="f-servicos",
                    options=[{"label": shorten_service_label(c), "value": c} for c in SERV_COLS if c in df.columns],
                    value=[],
                    style={"display": "grid", "gap": "6px"}
                ),

                html.Hr(),
                dcc.Checklist(
                    id="f-links",
                    options=[
                        {"label": "Tem Instagram", "value": "ig"},
                        {"label": "Tem site", "value": "site"},
                    ],
                    value=[]
                ),
            ], style={
                "overflowY": "auto",
                "paddingRight": "6px",
                "flex": "1 1 auto",
                "minHeight": 0
            }),

            html.Div([
                html.H5("Contribua com o mapeamento (Psicocult/Fiocruz)"),
                html.Div(
                    "Conhece uma associação de pacientes que ainda não está no painel? "
                    "Envie as informações pelo formulário abaixo para integrar o banco de dados da pesquisa.",
                    style={"fontSize": "12px", "opacity": 0.9, "marginTop": "6px", "lineHeight": "1.4"}
                ),
                html.A(
                    "➜ Indicar associação",
                    href=FORM_URL,
                    target="_blank",
                    style={
                        "backgroundColor": "#5B21B6",
                        "color": "white",
                        "padding": "10px 12px",
                        "borderRadius": "8px",
                        "textDecoration": "none",
                        "fontSize": "13px",
                        "fontWeight": "bold",
                        "display": "inline-block",
                        "marginTop": "10px"
                    }
                ),
                html.Div(
                    "As informações enviadas passam por verificação antes de serem incorporadas ao mapeamento.",
                    style={"fontSize": "11px", "color": "#555", "marginTop": "8px", "lineHeight": "1.3"}
                )
            ], style={
                "backgroundColor": "#F4EEFF",
                "border": "1px solid #D8C9FB",
                "padding": "12px",
                "borderRadius": "14px",
                "boxShadow": "0 6px 18px rgba(91,33,182,0.08)",
                "marginTop": "10px",
                "flex": "0 0 auto"
            }),

            html.Div(style={"height": "4px"}),

        ], style={
            "width": "360px",
            "padding": "16px",
            "borderRight": "1px solid #eee",
            "display": "flex",
            "flexDirection": "column",
            "height": "100%"
        }),

        html.Div([
            html.Div(id="kpis", style={"display": "grid", "gridTemplateColumns": "repeat(4, 1fr)", "gap": "12px"}),
            html.Br(),

            dcc.Tabs([
                dcc.Tab(label="Mapa", children=[
                    html.Div(
                        id="mapa-info",
                        style={"opacity": 0.75, "marginTop": "8px"}
                    ),
                    html.Div(
                        dcc.Graph(
                            id="mapa",
                            style={"height": "65vh", "backgroundColor": "#D3D3D3", "borderRadius": "12px"},
                            config={"displayModeBar": False}
                        ),
                        className="map-panel",
                        style={
                            "backgroundColor": "#D3D3D3",
                            "border": "1px solid #CFC3F2",
                            "borderRadius": "16px",
                            "padding": "8px",
                            "boxShadow": "0 8px 20px rgba(91,33,182,0.10)",
                            "marginTop": "8px"
                        }
                    ),

                    html.Hr(),
                    html.H4("Associações no recorte atual"),
                    dash_table.DataTable(
                        id="lista-mapa",
                        page_size=12,
                        sort_action="native",
                        style_table=LISTA_STYLE_TABLE,
                        style_cell=LISTA_STYLE_CELL,
                        style_header=LISTA_STYLE_HEADER,
                        style_data_conditional=[
                            {
                                "if": {
                                    "filter_query": "{status_verificacao} = \"Verificada\"",
                                    "column_id": COL_NOME
                                },
                                "color": "#5B21B6",
                                "fontWeight": "700"
                            }
                        ],
                        hidden_columns=["status_verificacao"],
                        markdown_options={"link_target": "_blank"},
                    ),
                ]),

                dcc.Tab(label="Estatísticas", children=[
                    html.Div([
                        html.Div(dcc.Graph(id="rank-mun"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="linha-fundacao"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                    ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginTop": "8px"}),

                    html.Div([
                        html.Div(dcc.Graph(id="rosca-cnpj"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-presenca-digital"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                    ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginTop": "12px"}),

                    html.H4("Serviços e produtos", style={"marginTop": "18px", "marginBottom": "8px", "color": "#3B0764"}),
                    html.Div([
                        html.Div(dcc.Graph(id="rosca-serv-0"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-1"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-2"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-3"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-4"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-5"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-6"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                        html.Div(dcc.Graph(id="rosca-serv-7"), style={"backgroundColor": "#FBF9FF", "border": "1px solid #D8C9FB", "borderRadius": "14px", "padding": "8px"}),
                    ], style={"display": "grid", "gridTemplateColumns": "repeat(2, minmax(280px, 1fr))", "gap": "12px", "marginTop": "8px", "marginBottom": "12px"}),
                ]),

                dcc.Tab(label="Tabela", children=[
                    html.Div([
                        html.Button("Baixar CSV filtrado", id="btn-download"),
                        dcc.Download(id="download")
                    ], style={"marginBottom": "10px"}),

                    dash_table.DataTable(
                        id="tabela",
                        page_size=25,
                        filter_action="native",
                        sort_action="native",
                        style_table={"overflowX": "auto"},
                        style_cell={"textAlign": "left", "minWidth": "160px", "maxWidth": "400px", "whiteSpace": "normal"},
                    )
                ])
            ])
        ], style={
            "flex": 1,
            "padding": "16px",
            "overflowY": "auto",
            "minHeight": 0
        }),

    ], style={
        "display": "flex",
        "height": f"calc(100vh - {HEADER_H})",
        "minHeight": 0
    })

])


@app.callback(
    Output("kpis", "children"),
    Output("mapa", "figure"),
    Output("mapa-info", "children"),
    Output("rank-mun", "figure"),
    Output("linha-fundacao", "figure"),
    Output("rosca-cnpj", "figure"),
    Output("rosca-presenca-digital", "figure"),
    Output("rosca-serv-0", "figure"),
    Output("rosca-serv-1", "figure"),
    Output("rosca-serv-2", "figure"),
    Output("rosca-serv-3", "figure"),
    Output("rosca-serv-4", "figure"),
    Output("rosca-serv-5", "figure"),
    Output("rosca-serv-6", "figure"),
    Output("rosca-serv-7", "figure"),
    Output("tabela", "data"),
    Output("tabela", "columns"),
    Output("lista-mapa", "data"),
    Output("lista-mapa", "columns"),
    Output("lista-mapa", "style_data_conditional"),
    Input("f-uf", "value"),
    Input("f-mun", "value"),
    Input("f-verificacao", "value"),
    Input("f-cnpj", "value"),
    Input("f-servicos", "value"),
    Input("f-links", "value"),
)
def update_dashboard(f_uf, f_mun, f_verificacao, f_cnpj, f_serv, f_links):
    d = df.copy()

    if f_uf:
        d = d[as_stripped(safe_col(d, COL_UF)).isin([str(x).strip() for x in f_uf])]
    if f_mun:
        d = d[as_stripped(safe_col(d, COL_MUN)).isin([str(x).strip() for x in f_mun])]

    if f_verificacao == "verificadas":
        d = d[d["associacao_verificada"] == True]
    elif f_verificacao == "nao_verificadas":
        d = d[d["associacao_verificada"] == False]

    if f_cnpj == "com_cnpj":
        d = d[d["tem_cnpj"] == True]
    elif f_cnpj == "sem_cnpj":
        d = d[d["tem_cnpj"] == False]

    if f_links:
        if "ig" in f_links:
            d = d[d["tem_instagram"] == True]
        if "site" in f_links:
            d = d[d["tem_site"] == True]

    for c in (f_serv or []):
        if c not in d.columns:
            continue
        if "atendimento médico" in c.lower() or "atendimento medico" in c.lower():
            d = d[d[c].isin(["sim", "MP"])]
        else:
            d = d[d[c].isin(["sim"])]

    total = len(d)
    n_ufs = (
        d[COL_UF].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        if COL_UF in d.columns else 0
    )
    n_muns = (
        d[COL_MUN].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        if COL_MUN in d.columns else 0
    )
    pct_cnpj = (d["tem_cnpj"].mean() * 100) if total else 0

    kpis = [
        html.Div([html.Div("Associações", style={"opacity": 0.7}),
                  html.H3(f"{total:,}".replace(",", "."))],
                 style={"border": "1px solid #D8C9FB", "borderRadius": "12px", "padding": "12px", "backgroundColor": "#FBF9FF", "boxShadow": "0 1px 4px rgba(0,0,0,0.04)"}),
        html.Div([html.Div("UFs", style={"opacity": 0.7}),
                  html.H3(str(n_ufs))],
                 style={"border": "1px solid #D8C9FB", "borderRadius": "12px", "padding": "12px", "backgroundColor": "#FBF9FF", "boxShadow": "0 1px 4px rgba(0,0,0,0.04)"}),
        html.Div([html.Div("Municípios", style={"opacity": 0.7}),
                  html.H3(str(n_muns))],
                 style={"border": "1px solid #D8C9FB", "borderRadius": "12px", "padding": "12px", "backgroundColor": "#FBF9FF", "boxShadow": "0 1px 4px rgba(0,0,0,0.04)"}),
        html.Div([html.Div("Possui CNPJ", style={"opacity": 0.7}),
                  html.H3(f"{pct_cnpj:.0f}%")],
                 style={"border": "1px solid #D8C9FB", "borderRadius": "12px", "padding": "12px", "backgroundColor": "#FBF9FF", "boxShadow": "0 1px 4px rgba(0,0,0,0.04)"}),
    ]

    # mapa dinâmico (UF / municípios)
    try:
        ufs_filtradas = []
        if f_uf:
            ufs_filtradas = [
                UF_SIGLA.get(str(x).strip(), str(x).strip().upper())
                for x in f_uf if str(x).strip()
            ]
            ufs_filtradas = [x for x in ufs_filtradas if x and x != "NONE"]

        usar_mapa_municipal = len(ufs_filtradas) == 1
        mapa_info = (
            "Sem filtro de UF: exibe o mapa do Brasil por estado."
            if not ufs_filtradas else
            "1 UF selecionada: exibe o mapa municipal do estado filtrado."
            if usar_mapa_municipal else
            "Mais de 1 UF selecionada: exibe o mapa agregado por estado para manter a visualização leve."
        )

        if usar_mapa_municipal:
            uf_sel = ufs_filtradas[0]
            geojson_mun = get_mun_geojson_for_uf(uf_sel)

            if not geojson_mun:
                fig_mapa = blank_fig(f"Mapa municipal indisponível para UF={uf_sel}.")
            else:
                prop_key = guess_prop_key(geojson_mun)
                if not prop_key:
                    fig_mapa = blank_fig("Não consegui identificar o campo de nome do município no GeoJSON.")
                else:
                    d["_mun_norm"] = safe_col(d, COL_MUN).apply(norm_mun_name)

                    por_mun = (
                        d.groupby("_mun_norm")
                        .size()
                        .reset_index(name="n")
                        .rename(columns={"_mun_norm": "mun_norm"})
                    )

                    all_muns_geo = []
                    for ft in geojson_mun.get("features", []):
                        name = ft.get("properties", {}).get(prop_key, "")
                        if name:
                            all_muns_geo.append({
                                "mun_geo": name,
                                "mun_norm": norm_mun_name(name)
                            })

                    base_geo = pd.DataFrame(all_muns_geo).drop_duplicates(subset=["mun_geo"])

                    if base_geo.empty:
                        fig_mapa = blank_fig(f"GeoJSON municipal vazio para UF={uf_sel}.")
                    else:
                        base_geo = base_geo.merge(
                            por_mun[["mun_norm", "n"]],
                            on="mun_norm",
                            how="left"
                        )
                        base_geo["n"] = base_geo["n"].fillna(0).astype(int)

                        fig_mapa = px.choropleth(
                            base_geo,
                            geojson=geojson_mun,
                            locations="mun_geo",
                            featureidkey=f"properties.{prop_key}",
                            color="n",
                            title=f"Concentração de associações por município — {uf_sel}",
                            color_continuous_scale=PURPLE_SCALE,
                        )

                        fig_mapa.update_traces(
                            marker_line_color="rgba(91,33,182,0.45)",
                            marker_line_width=0.65
                        )
                        fig_mapa.update_geos(
                            fitbounds="locations",
                            visible=False,
                            bgcolor="#D3D3D3"
                        )
                        fig_mapa = apply_map_background(fig_mapa, geo_bg="#D3D3D3")
                        fig_mapa = apply_plot_theme(fig_mapa, paper_bg="#D3D3D3", plot_bg="#D3D3D3")

        else:
            por_uf = (
                d.groupby("uf_sigla")
                .size()
                .reset_index(name="n")
                .dropna(subset=["uf_sigla"])
            )

            if por_uf.empty:
                fig_mapa = blank_fig("Mapa: sem dados após filtros.")
            else:
                fig_mapa = px.choropleth(
                    por_uf,
                    geojson=BRAZIL_STATES_GEOJSON,
                    locations="uf_sigla",
                    featureidkey="properties.sigla",
                    color="n",
                    title="Distribuição de associações por UF",
                    color_continuous_scale=PURPLE_SCALE,
                )
                fig_mapa.update_traces(
                    marker_line_color="rgba(91,33,182,0.78)",
                    marker_line_width=1.35
                )
                fig_mapa.update_geos(
                    fitbounds="locations",
                    visible=False,
                    bgcolor="#D3D3D3",
                    projection_scale=1
                )
                fig_mapa = apply_map_background(fig_mapa, geo_bg="#D3D3D3")
                fig_mapa = apply_plot_theme(fig_mapa, paper_bg="#D3D3D3", plot_bg="#D3D3D3")

    except Exception as e:
        print("ERRO NO MAPA DINÂMICO:", repr(e))
        fig_mapa = blank_fig(f"Mapa indisponível (erro): {type(e).__name__}")
        mapa_info = "Não foi possível renderizar o mapa atual."
    # estatísticas
    try:
        if COL_MUN in d.columns:
            rmun = (
                d[COL_MUN]
                .dropna()
                .astype(str)
                .str.strip()
                .replace("", pd.NA)
                .dropna()
                .value_counts()
                .head(20)
                .reset_index()
            )
            rmun.columns = [COL_MUN, "n"]
            fig_mun = px.bar(
                rmun,
                x="n",
                y=COL_MUN,
                orientation="h",
                title="Top 20 municípios por número de associações",
                color="n",
                color_continuous_scale=PURPLE_SCALE,
            )
            fig_mun.update_layout(coloraxis_showscale=False, paper_bgcolor="#FBF9FF", plot_bgcolor="#FBF9FF")
            fig_mun = apply_plot_theme(fig_mun)
        else:
            fig_mun = blank_fig("Ranking de municípios indisponível.")
    except Exception as e:
        print("ERRO NO RANK MUNICIPIO:", repr(e))
        fig_mun = blank_fig(f"Ranking municípios (erro): {type(e).__name__}")

    try:
        fund = d[["ano_fundacao"]].copy() if "ano_fundacao" in d.columns else pd.DataFrame()
        fund = fund.dropna()
        if not fund.empty:
            fund["ano_fundacao"] = fund["ano_fundacao"].astype(int)
            fund = fund.groupby("ano_fundacao").size().reset_index(name="n").sort_values("ano_fundacao")
            fig_fund = px.line(
                fund,
                x="ano_fundacao",
                y="n",
                markers=True,
                title="Linha do tempo das fundações por ano",
            )
            fig_fund.update_traces(line={"width": 3}, marker={"size": 9})
            fig_fund.update_layout(
                xaxis_title="Ano de fundação",
                yaxis_title="Nº de associações",
                paper_bgcolor="#FBF9FF",
                plot_bgcolor="#FBF9FF",
            )
            fig_fund = apply_plot_theme(fig_fund)
        else:
            fig_fund = blank_fig("Linha do tempo das fundações indisponível para o recorte atual.")
    except Exception as e:
        print("ERRO NA LINHA DO TEMPO:", repr(e))
        fig_fund = blank_fig(f"Linha do tempo (erro): {type(e).__name__}")

    try:
        cnpj_counts = pd.DataFrame({
            "categoria": ["Com CNPJ", "Sem CNPJ"],
            "n": [int(d.get("tem_cnpj", pd.Series(dtype=bool)).sum()), int((~d.get("tem_cnpj", pd.Series(dtype=bool))).sum()) if "tem_cnpj" in d.columns else 0]
        })
        fig_cnpj = make_donut_from_counts(cnpj_counts, "categoria", "n", "CNPJ das associações")
    except Exception as e:
        print("ERRO NA ROSCA CNPJ:", repr(e))
        fig_cnpj = blank_fig(f"CNPJ (erro): {type(e).__name__}")

    try:
        presenca = pd.Series("Nenhum", index=d.index)
        if "tem_instagram" in d.columns and "tem_site" in d.columns:
            presenca = pd.Series(index=d.index, dtype=object)
            presenca[(d["tem_instagram"]) & (d["tem_site"])] = "Instagram + Site"
            presenca[(d["tem_instagram"]) & (~d["tem_site"])] = "Só Instagram"
            presenca[(~d["tem_instagram"]) & (d["tem_site"])] = "Só Site"
            presenca[(~d["tem_instagram"]) & (~d["tem_site"])] = "Nenhum"
        pres_df = presenca.value_counts(dropna=False).rename_axis("categoria").reset_index(name="n")
        fig_presenca = make_donut_from_counts(pres_df, "categoria", "n", "Presença digital")
    except Exception as e:
        print("ERRO NA ROSCA PRESENÇA DIGITAL:", repr(e))
        fig_presenca = blank_fig(f"Presença digital (erro): {type(e).__name__}")

    figs_serv = []
    for serv_col in SERV_COLS:
        try:
            if serv_col in d.columns:
                serv_df = (
                    d[serv_col]
                    .fillna("NI")
                    .astype(str)
                    .str.strip()
                    .replace("", "NI")
                    .value_counts()
                    .rename_axis("categoria")
                    .reset_index(name="n")
                )
                serv_df["categoria"] = serv_df["categoria"].replace({"sim": "Sim", "não": "Não", "NI": "NI", "MP": "MP"})
                fig_serv = make_donut_from_counts(serv_df, "categoria", "n", shorten_service_label(serv_col))
            else:
                fig_serv = blank_fig(f"{shorten_service_label(serv_col)}: coluna indisponível.")
        except Exception as e:
            print("ERRO NA ROSCA DE SERVIÇO:", serv_col, repr(e))
            fig_serv = blank_fig(f"{shorten_service_label(serv_col)} (erro): {type(e).__name__}")
        figs_serv.append(fig_serv)

    # tabela
    table_cols = [
        c for c in [
            COL_ID, COL_NOME, COL_SIGLA, COL_UF, COL_MUN, "tem_instagram", "tem_site",
            *[c for c in SERV_COLS if c in d.columns],
            COL_OBS
        ] if c in d.columns
    ]
    d_show = d[table_cols].copy()
    columns = [{"name": c, "id": c} for c in d_show.columns]
    data = d_show.to_dict("records")

    # listas abaixo dos mapas
    try:
        base_cols = [c for c in [COL_NOME, COL_SIGLA, COL_UF, COL_MUN, "status_verificacao"] if c in d.columns]
        pull_cols = base_cols[:]
        if COL_INSTAGRAM in d.columns:
            pull_cols.append(COL_INSTAGRAM)
        if COL_SITE in d.columns:
            pull_cols.append(COL_SITE)

        d_list = d[pull_cols].copy() if pull_cols else pd.DataFrame()

        if not d_list.empty:
            d_list["Instagram"] = d_list[COL_INSTAGRAM].apply(lambda x: make_md_link(x, "Instagram")) if COL_INSTAGRAM in d_list.columns else ""
            d_list["Site"] = d_list[COL_SITE].apply(lambda x: make_md_link(x, "Site")) if COL_SITE in d_list.columns else ""

            show_cols = [c for c in [COL_NOME, COL_SIGLA, COL_UF, COL_MUN, "Instagram", "Site", "status_verificacao"] if c in d_list.columns]
            d_list = d_list[show_cols].copy()

            if COL_NOME in d_list.columns:
                d_list = d_list.sort_values(COL_NOME, kind="stable")
            d_list = d_list.head(300)

            lista_columns = []
            for c in d_list.columns:
                col = {"name": c, "id": c}
                if c in ["Instagram", "Site"]:
                    col["presentation"] = "markdown"
                lista_columns.append(col)

            lista_data = d_list.to_dict("records")
        else:
            lista_columns = [{"name": "Sem dados", "id": "Sem dados"}]
            lista_data = [{"Sem dados": "Nenhuma associação no recorte atual."}]

        lista_style_data_conditional = [
            {
                "if": {
                    "filter_query": "{status_verificacao} = \"Verificada\"",
                    "column_id": COL_NOME
                },
                "color": "#5B21B6",
                "fontWeight": "700"
            }
        ]

    except Exception as e:
        print("ERRO NA LISTA ABAIXO DO MAPA:", repr(e))
        lista_columns = [{"name": "Erro", "id": "Erro"}]
        lista_data = [{"Erro": f"Falha ao montar lista: {type(e).__name__}"}]
        lista_style_data_conditional = []

    return (
        kpis, fig_mapa, mapa_info, fig_mun,
        fig_fund, fig_cnpj, fig_presenca,
        figs_serv[0], figs_serv[1], figs_serv[2], figs_serv[3],
        figs_serv[4], figs_serv[5], figs_serv[6], figs_serv[7],
        data, columns,
        lista_data, lista_columns, lista_style_data_conditional
    )


@app.callback(
    Output("download", "data"),
    Input("btn-download", "n_clicks"),
    State("tabela", "data"),
    prevent_initial_call=True
)
def download_csv(_, table_data):
    dld = pd.DataFrame(table_data)
    return dcc.send_data_frame(dld.to_csv, "associacoes_filtradas.csv", index=False)


server = app.server

if __name__ == "__main__":
    app.run(debug=True)
