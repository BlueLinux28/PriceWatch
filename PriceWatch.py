import os
import re
import json
import glob
import queue
import hmac
import hashlib
import sqlite3
import time
import threading
import webbrowser
from datetime import datetime

import pandas as pd
import customtkinter as ctk
from tkinter import messagebox, filedialog

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# ==========================================
# PALETA / TEMA
# ==========================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG = ("#F3F4F8", "#12131A")
SIDEBAR = ("#FFFFFF", "#171821")
CARD = ("#FFFFFF", "#1E2030")
CARD_ALT = ("#EEF0F6", "#262840")
TEXT = ("#111827", "#F3F4F6")
MUTED = ("#6B7280", "#9CA3AF")
ACCENT = "#6C5CE7"
ACCENT_HOVER = "#5948D3"
GREEN = "#00B894"
RED = "#E5484D"
AMBER = "#F59E0B"

# ==========================================
# 1. BANCO DE DADOS (SQLite)
# ==========================================
DB_NAME = "historico.db"
EXPORT_DIR = "exportacoes"


def _hash_senha(senha: str, salt: str = None) -> str:
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return f"pbkdf2${salt}${h}"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS buscas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termo TEXT NOT NULL,
            data_hora DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            busca_id INTEGER,
            titulo TEXT,
            preco REAL,
            link TEXT,
            FOREIGN KEY (busca_id) REFERENCES buscas (id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL
        )
    """)
    cur.execute("SELECT COUNT(*) FROM admin")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO admin (usuario, senha) VALUES (?, ?)",
                    ("root master", _hash_senha("root")))
    conn.commit()
    conn.close()


def verificar_login_admin(usuario: str, senha: str) -> bool:
    """Confere o login. Senhas antigas em texto puro são aceitas uma vez
    e migradas automaticamente para hash."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, senha FROM admin WHERE usuario = ?", (usuario,))
    row = cur.fetchone()
    ok = False
    if row:
        admin_id, guardada = row
        if guardada.startswith("pbkdf2$"):
            _, salt, _ = guardada.split("$")
            ok = hmac.compare_digest(_hash_senha(senha, salt), guardada)
        elif hmac.compare_digest(guardada, senha):
            ok = True
            cur.execute("UPDATE admin SET senha = ? WHERE id = ?", (_hash_senha(senha), admin_id))
            conn.commit()
    conn.close()
    return ok


def salvar_no_banco(termo: str, lista_produtos: list):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    data = datetime.now().strftime("%d/%m/%Y %H:%M")
    cur.execute("INSERT INTO buscas (termo, data_hora) VALUES (?, ?)", (termo, data))
    busca_id = cur.lastrowid
    for item in lista_produtos:
        cur.execute(
            "INSERT INTO produtos (busca_id, titulo, preco, link) VALUES (?, ?, ?, ?)",
            (busca_id, item["titulo"], item["preco"], item["link"]),
        )
    conn.commit()
    conn.close()


def obter_historico_completo() -> list:
    """Retorna o histórico como lista de dicts padronizados."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT b.data_hora, b.termo, p.titulo, p.preco, p.link
        FROM produtos p JOIN buscas b ON p.busca_id = b.id
        ORDER BY p.id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [{"data": r[0], "termo": r[1], "titulo": r[2], "preco": r[3] or 0.0, "link": r[4]} for r in rows]


def obter_df_historico() -> pd.DataFrame:
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("""
        SELECT b.data_hora AS 'Data/Hora', b.termo AS 'Termo Pesquisado',
               p.titulo AS 'Produto', p.preco AS 'Preço (R$)', p.link AS 'Link'
        FROM produtos p JOIN buscas b ON p.busca_id = b.id
        ORDER BY p.id DESC
    """, conn)
    conn.close()
    return df


# ==========================================
# 2. AUTOMAÇÃO WEB (Selenium)
# ==========================================
def executar_scraping(termo: str, limite: int = 10, callback_status=None):
    if callback_status:
        callback_status("Iniciando Chrome...")

    options = Options()
    options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(options=options)
    produtos = []

    try:
        if callback_status:
            callback_status(f"Acessando Mercado Livre para '{termo}'...")

        termo_url = termo.replace(" ", "-").lower()
        driver.get(f"https://lista.mercadolivre.com.br/{termo_url}")
        time.sleep(3)

        cards, tentativas = [], 0
        while len(cards) < limite and tentativas < 12:
            driver.execute_script("window.scrollBy(0, 1200);")
            time.sleep(1.2)
            cards = driver.find_elements(
                By.CSS_SELECTOR,
                "li.ui-search-layout__item, div.poly-card, div.ui-search-result__content, .ui-search-result",
            )
            tentativas += 1
            if callback_status:
                callback_status(f"Carregando itens... {min(len(cards), limite)}/{limite}")

        if callback_status:
            callback_status(f"Lendo {min(len(cards), limite)} itens e extraindo preços...")

        for card in cards[:limite]:
            try:
                titulo = ""
                for sel in [".poly-component__title", ".ui-search-item__title", "h2", "a.poly-component__title"]:
                    el = card.find_elements(By.CSS_SELECTOR, sel)
                    if el and el[0].text.strip():
                        titulo = el[0].text.strip()
                        break

                link_el = card.find_elements(By.TAG_NAME, "a")
                link = link_el[0].get_attribute("href") if link_el else ""

                preco = 0.0
                for sel in [
                    ".poly-price__current .andromeda-money-amount__fraction",
                    ".poly-component__price .andromeda-money-amount__fraction",
                    ".andromeda-money-amount__fraction",
                    ".price-tag-fraction",
                    ".poly-price__current",
                ]:
                    el = card.find_elements(By.CSS_SELECTOR, sel)
                    if el and el[0].text.strip():
                        txt = el[0].text.replace(".", "").replace("R$", "").strip()
                        m = re.search(r"\d+", txt)
                        if m:
                            preco = float(m.group())
                            break

                if preco == 0.0:
                    m = re.search(r"R\$\s*([\d\.]+)", card.text)
                    if m:
                        try:
                            preco = float(m.group(1).replace(".", ""))
                        except Exception:
                            pass

                # Centavos (quando o site os exibe separados)
                if preco > 0:
                    cents = card.find_elements(By.CSS_SELECTOR, ".poly-price__current .andromeda-money-amount__cents")
                    if cents and cents[0].text.strip().isdigit():
                        preco += int(cents[0].text.strip()) / 100

                if titulo:
                    produtos.append({"titulo": titulo, "preco": preco, "link": link})
            except Exception:
                continue
    finally:
        driver.quit()

    return produtos


# ==========================================
# UTILITÁRIOS
# ==========================================
def fmt_brl(v: float) -> str:
    if not v or v <= 0:
        return "N/D"
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def to_float(v) -> float:
    try:
        f = float(v)
        return 0.0 if f != f else f  # trata NaN
    except (TypeError, ValueError):
        return 0.0


def listar_exportacoes() -> list:
    padroes = [os.path.join(EXPORT_DIR, "historico_exportado_*.*"), "historico_exportado_*.*"]
    arquivos = []
    for p in padroes:
        arquivos += [f for f in glob.glob(p) if f.lower().endswith((".xlsx", ".json"))]
    return sorted(set(arquivos), key=os.path.getmtime, reverse=True)


# ==========================================
# 3. COMPONENTES DE INTERFACE
# ==========================================
class StatCard(ctk.CTkFrame):
    def __init__(self, master, titulo, cor):
        super().__init__(master, fg_color=CARD, corner_radius=14)
        barra = ctk.CTkFrame(self, width=5, fg_color=cor, corner_radius=3)
        barra.pack(side="left", fill="y", padx=(10, 0), pady=12)
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(side="left", padx=12, pady=10, fill="x", expand=True)
        ctk.CTkLabel(box, text=titulo, text_color=MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.valor = ctk.CTkLabel(box, text="—", text_color=TEXT, font=ctk.CTkFont(size=20, weight="bold"))
        self.valor.pack(anchor="w")

    def set(self, texto):
        self.valor.configure(text=texto)


class ResultsView(ctk.CTkFrame):
    """Lista de produtos em cartões, com estatísticas, filtro e ordenação."""
    MAX_RENDER = 300
    SORTS = ["Ordem original", "Menor preço", "Maior preço", "Título A–Z"]

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.rows = []
        self._token = 0

        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 10))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1, uniform="s")
        self.st_total = StatCard(stats, "Produtos", ACCENT)
        self.st_min = StatCard(stats, "Menor preço", GREEN)
        self.st_avg = StatCard(stats, "Preço médio", AMBER)
        self.st_max = StatCard(stats, "Maior preço", RED)
        for i, s in enumerate((self.st_total, self.st_min, self.st_avg, self.st_max)):
            s.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        self.ent_filtro = ctk.CTkEntry(bar, placeholder_text="🔎  Filtrar por título ou termo...",
                                       height=36, corner_radius=10)
        self.ent_filtro.pack(side="left", fill="x", expand=True)
        self.ent_filtro.bind("<KeyRelease>", lambda e: self.refresh())
        self.menu_ordem = ctk.CTkOptionMenu(bar, values=self.SORTS, width=150, height=36,
                                            fg_color=CARD, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                            text_color=TEXT, command=lambda v: self.refresh())
        self.menu_ordem.pack(side="left", padx=(8, 0))
        self.lbl_info = ctk.CTkLabel(bar, text="", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.lbl_info.pack(side="left", padx=(12, 0))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=CARD_ALT, corner_radius=14)
        self.scroll.pack(fill="both", expand=True)

    def set_data(self, rows):
        self.rows = rows
        self.refresh()

    def _filtrados(self):
        q = self.ent_filtro.get().strip().lower()
        rows = [r for r in self.rows if not q or q in str(r["titulo"]).lower() or q in str(r["termo"]).lower()]
        ordem = self.menu_ordem.get()
        if ordem == "Menor preço":
            rows = sorted(rows, key=lambda r: (r["preco"] <= 0, r["preco"]))
        elif ordem == "Maior preço":
            rows = sorted(rows, key=lambda r: -r["preco"])
        elif ordem == "Título A–Z":
            rows = sorted(rows, key=lambda r: str(r["titulo"]).lower())
        return rows

    def refresh(self):
        rows = self._filtrados()
        precos = [r["preco"] for r in rows if r["preco"] > 0]
        self.st_total.set(str(len(rows)))
        self.st_min.set(fmt_brl(min(precos)) if precos else "—")
        self.st_avg.set(fmt_brl(sum(precos) / len(precos)) if precos else "—")
        self.st_max.set(fmt_brl(max(precos)) if precos else "—")

        self._token += 1
        for w in self.scroll.winfo_children():
            w.destroy()

        if not rows:
            ctk.CTkLabel(self.scroll, text="📭\n\nNada para mostrar por aqui ainda.",
                         text_color=MUTED, font=ctk.CTkFont(size=14)).pack(pady=60)
            self.lbl_info.configure(text="")
            return

        mostrar = rows[: self.MAX_RENDER]
        self.lbl_info.configure(
            text=f"Exibindo {len(mostrar)} de {len(rows)}" if len(rows) > len(mostrar) else "")
        self._render_lote(mostrar, 0, self._token)

    def _render_lote(self, rows, inicio, token, tamanho=15):
        if token != self._token:
            return
        for r in rows[inicio: inicio + tamanho]:
            self._card(r)
        if inicio + tamanho < len(rows):
            self.after(10, lambda: self._render_lote(rows, inicio + tamanho, token))

    def _card(self, r):
        card = ctk.CTkFrame(self.scroll, fg_color=CARD, corner_radius=12)
        card.pack(fill="x", padx=6, pady=4)
        card.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=0, sticky="nsew", padx=(14, 6), pady=10)
        ctk.CTkLabel(info, text=r["titulo"], anchor="w", justify="left", wraplength=470,
                     text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        meta = f"🏷️ {r['termo']}   •   🕒 {r['data']}"
        ctk.CTkLabel(info, text=meta, text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 0))

        lado = ctk.CTkFrame(card, fg_color="transparent")
        lado.grid(row=0, column=1, padx=(6, 14), pady=10)
        cor_preco = GREEN if r["preco"] > 0 else MUTED
        ctk.CTkLabel(lado, text=fmt_brl(r["preco"]), text_color=cor_preco,
                     font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="e")
        botoes = ctk.CTkFrame(lado, fg_color="transparent")
        botoes.pack(anchor="e", pady=(6, 0))
        link = r.get("link") or ""
        ctk.CTkButton(botoes, text="Abrir ↗", width=64, height=26, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      state="normal" if link.startswith("http") else "disabled",
                      command=lambda l=link: webbrowser.open(l)).pack(side="left")
        ctk.CTkButton(botoes, text="Copiar", width=60, height=26, corner_radius=8,
                      fg_color=CARD_ALT, hover_color=("#DDE0EA", "#33365A"), text_color=TEXT,
                      command=lambda l=link: self.app.copiar(l)).pack(side="left", padx=(6, 0))


# ==========================================
# 4. APLICAÇÃO PRINCIPAL
# ==========================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PriceWatch - Monitor De Preços Do Mercado Livre")
        self.geometry("1180x720")
        self.minsize(980, 620)
        self.configure(fg_color=BG)

        init_db()
        os.makedirs(EXPORT_DIR, exist_ok=True)

        self.fila = queue.Queue()
        self.paginas = {}
        self.nav_btns = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._criar_sidebar()
        self.area = ctk.CTkFrame(self, fg_color="transparent")
        self.area.grid(row=0, column=1, sticky="nsew", padx=22, pady=18)
        self.area.grid_columnconfigure(0, weight=1)
        self.area.grid_rowconfigure(0, weight=1)

        self._pagina_pesquisa()
        self._pagina_historico()
        self._pagina_exportar()
        self.mostrar("pesquisa")

        self.after(100, self._processar_fila)

    # ---------- infraestrutura ----------
    def ui(self, fn, *args):
        """Agenda uma chamada de UI a partir de outra thread (thread-safe)."""
        self.fila.put((fn, args))

    def _processar_fila(self):
        try:
            while True:
                fn, args = self.fila.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)

    def toast(self, msg, tipo="ok"):
        cor = {"ok": GREEN, "erro": RED, "aviso": AMBER}.get(tipo, ACCENT)
        box = ctk.CTkFrame(self, fg_color=cor, corner_radius=12)
        ctk.CTkLabel(box, text=msg, text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
                     wraplength=380, justify="left").pack(padx=18, pady=10)
        box.place(relx=1.0, rely=1.0, x=-22, y=-22, anchor="se")
        box.lift()
        self.after(3800, box.destroy)

    def copiar(self, texto):
        if texto:
            self.clipboard_clear()
            self.clipboard_append(texto)
            self.toast("Link copiado!", "info")

    # ---------- sidebar ----------
    def _criar_sidebar(self):
        sb = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=SIDEBAR)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)

        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=18, pady=(26, 26))
        ctk.CTkLabel(logo, text="🛍️", font=ctk.CTkFont(size=30)).pack(side="left")
        tx = ctk.CTkFrame(logo, fg_color="transparent")
        tx.pack(side="left", padx=10)
        ctk.CTkLabel(tx, text="PriceWatch", font=ctk.CTkFont(size=19, weight="bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(tx, text="Mercado Livre", font=ctk.CTkFont(size=11), text_color=MUTED).pack(anchor="w")

        itens = [("pesquisa", "🔍   Pesquisar"), ("historico", "📜   Histórico"), ("exportar", "📤   Exportar")]
        for chave, texto in itens:
            b = ctk.CTkButton(sb, text=texto, anchor="w", height=42, corner_radius=10,
                              fg_color="transparent", hover_color=CARD_ALT, text_color=TEXT,
                              font=ctk.CTkFont(size=14), command=lambda k=chave: self.mostrar(k))
            b.pack(fill="x", padx=14, pady=3)
            self.nav_btns[chave] = b

        ctk.CTkButton(sb, text="🔐   Painel Admin", anchor="w", height=42, corner_radius=10,
                      fg_color="transparent", hover_color=CARD_ALT, text_color=MUTED,
                      font=ctk.CTkFont(size=14), command=self.abrir_login_admin
                      ).pack(side="bottom", fill="x", padx=14, pady=(3, 20))

        self.sw_tema = ctk.CTkSwitch(sb, text="Modo escuro", progress_color=ACCENT,
                                     command=self._alternar_tema, text_color=MUTED)
        self.sw_tema.select()
        self.sw_tema.pack(side="bottom", padx=20, pady=(0, 10), anchor="w")

    def _alternar_tema(self):
        ctk.set_appearance_mode("dark" if self.sw_tema.get() else "light")

    def mostrar(self, nome):
        for k, f in self.paginas.items():
            f.grid_remove()
            self.nav_btns[k].configure(fg_color="transparent", text_color=TEXT)
        self.paginas[nome].grid(row=0, column=0, sticky="nsew")
        self.nav_btns[nome].configure(fg_color=ACCENT, text_color="white")
        if nome == "exportar":
            self._atualizar_lista_exportacoes()

    def _cabecalho(self, pai, titulo, subtitulo):
        h = ctk.CTkFrame(pai, fg_color="transparent")
        h.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(h, text=titulo, font=ctk.CTkFont(size=26, weight="bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(h, text=subtitulo, font=ctk.CTkFont(size=13), text_color=MUTED).pack(anchor="w")
        return h

    # ---------- página: pesquisa ----------
    def _pagina_pesquisa(self):
        p = ctk.CTkFrame(self.area, fg_color="transparent")
        self.paginas["pesquisa"] = p
        self._cabecalho(p, "Pesquisar produtos", "Compare preços em segundos e guarde tudo no histórico.")

        card = ctk.CTkFrame(p, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", pady=(0, 12))

        linha = ctk.CTkFrame(card, fg_color="transparent")
        linha.pack(fill="x", padx=16, pady=(16, 8))
        self.ent_busca = ctk.CTkEntry(linha, placeholder_text="O que você quer encontrar? Ex: fone bluetooth",
                                      height=44, corner_radius=12, font=ctk.CTkFont(size=14))
        self.ent_busca.pack(side="left", fill="x", expand=True)
        self.ent_busca.bind("<Return>", lambda e: self.iniciar_busca())
        self.btn_buscar = ctk.CTkButton(linha, text="Pesquisar", width=140, height=44, corner_radius=12,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                        font=ctk.CTkFont(size=14, weight="bold"), command=self.iniciar_busca)
        self.btn_buscar.pack(side="left", padx=(10, 0))

        qtd = ctk.CTkFrame(card, fg_color="transparent")
        qtd.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(qtd, text="Quantidade de produtos", text_color=MUTED).pack(side="left")
        self.lbl_qtd = ctk.CTkLabel(qtd, text="10", width=40, text_color=ACCENT,
                                    font=ctk.CTkFont(size=15, weight="bold"))
        self.lbl_qtd.pack(side="right")
        self.slider = ctk.CTkSlider(card, from_=1, to=120, number_of_steps=119, progress_color=ACCENT,
                                    button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                    command=lambda v: self.lbl_qtd.configure(text=str(int(v))))
        self.slider.set(10)
        self.slider.pack(fill="x", padx=16, pady=(0, 10))

        self.progress = ctk.CTkProgressBar(card, mode="indeterminate", progress_color=ACCENT, height=5)
        self.lbl_status = ctk.CTkLabel(card, text="● Pronto", text_color=MUTED, anchor="w",
                                       font=ctk.CTkFont(size=12))
        self.lbl_status.pack(fill="x", padx=18, pady=(0, 14))

        self.res_busca = ResultsView(p, self)
        self.res_busca.pack(fill="both", expand=True)

    def set_status(self, msg, cor=MUTED):
        self.lbl_status.configure(text=f"● {msg}", text_color=cor)

    def _busy(self, ligado: bool):
        if ligado:
            self.btn_buscar.configure(state="disabled", text="Buscando...")
            self.progress.pack(fill="x", padx=18, pady=(0, 6), before=self.lbl_status)
            self.progress.start()
        else:
            self.btn_buscar.configure(state="normal", text="Pesquisar")
            self.progress.stop()
            self.progress.pack_forget()

    def iniciar_busca(self):
        termo = self.ent_busca.get().strip()
        if not termo:
            self.toast("Digite um produto para pesquisar.", "aviso")
            return
        quantidade = int(self.slider.get())
        self._busy(True)
        threading.Thread(target=self._rodar_automacao, args=(termo, quantidade), daemon=True).start()

    def _rodar_automacao(self, termo, quantidade):
        try:
            res = executar_scraping(termo, limite=quantidade,
                                    callback_status=lambda m: self.ui(self.set_status, m, ACCENT))
            if res:
                self.ui(self.set_status, "Salvando no banco SQLite...", ACCENT)
                salvar_no_banco(termo, res)
                data = datetime.now().strftime("%d/%m/%Y %H:%M")
                rows = [{"data": data, "termo": termo, "titulo": r["titulo"],
                         "preco": r["preco"], "link": r["link"]} for r in res]
                self.ui(self.res_busca.set_data, rows)
                self.ui(self.set_status, f"Concluído — {len(rows)} produtos gravados no banco.", GREEN)
                self.ui(self.toast, f"{len(rows)} produtos encontrados!", "ok")
            else:
                self.ui(self.set_status, "Nenhum resultado encontrado.", AMBER)
                self.ui(self.toast, "Nenhum resultado foi retornado.", "aviso")
        except Exception as e:
            self.ui(self.set_status, "Erro durante a execução.", RED)
            self.ui(messagebox.showerror, "Erro", f"Ocorreu um erro: {e}")
        finally:
            self.ui(self._busy, False)

    # ---------- página: histórico ----------
    def _pagina_historico(self):
        p = ctk.CTkFrame(self.area, fg_color="transparent")
        self.paginas["historico"] = p
        h = self._cabecalho(p, "Histórico", "Tudo o que já foi pesquisado, ou o conteúdo de uma planilha/JSON.")

        ctk.CTkButton(h, text="📂  Abrir Excel / JSON", height=36, corner_radius=10,
                      fg_color=CARD, hover_color=CARD_ALT, text_color=TEXT,
                      command=self.abrir_arquivo).pack(side="right")
        ctk.CTkButton(h, text="🔄  Carregar do banco", height=36, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.carregar_historico_banco).pack(side="right", padx=8)

        self.lbl_fonte = ctk.CTkLabel(p, text="Clique em “Carregar do banco” para ver o histórico.",
                                      text_color=MUTED, font=ctk.CTkFont(size=12, slant="italic"))
        self.lbl_fonte.pack(anchor="w", pady=(0, 8))

        self.res_hist = ResultsView(p, self)
        self.res_hist.pack(fill="both", expand=True)

    def carregar_historico_banco(self):
        rows = obter_historico_completo()
        if not rows:
            self.toast("O banco de dados está vazio.", "aviso")
        self.res_hist.set_data(rows)
        self.lbl_fonte.configure(text=f"Fonte: banco 'historico.db' — {len(rows)} registros")

    def abrir_arquivo(self):
        caminho = filedialog.askopenfilename(
            title="Selecione um arquivo",
            filetypes=[("Excel ou JSON", "*.xlsx *.xls *.json"), ("Todos", "*.*")])
        if caminho:
            self.carregar_arquivo(caminho)

    def carregar_arquivo(self, caminho):
        try:
            if caminho.lower().endswith(".json"):
                with open(caminho, "r", encoding="utf-8") as f:
                    registros = json.load(f)
            else:
                registros = pd.read_excel(caminho).to_dict(orient="records")

            rows = []
            for r in registros:
                rows.append({
                    "data": r.get("Data/Hora", "-"),
                    "termo": r.get("Termo Pesquisado", r.get("Termo", "-")),
                    "titulo": r.get("Produto", r.get("titulo", "-")),
                    "preco": to_float(r.get("Preço (R$)", r.get("preco", 0.0))),
                    "link": str(r.get("Link", r.get("link", "")) or ""),
                })
            self.mostrar("historico")
            self.res_hist.set_data(rows)
            self.lbl_fonte.configure(text=f"Fonte: arquivo {os.path.basename(caminho)} — {len(rows)} registros")
            self.toast(f"'{os.path.basename(caminho)}' carregado!", "ok")
        except Exception as e:
            messagebox.showerror("Erro ao ler arquivo", f"Não foi possível ler o arquivo:\n{e}")

    # ---------- página: exportar ----------
    def _pagina_exportar(self):
        p = ctk.CTkFrame(self.area, fg_color="transparent")
        self.paginas["exportar"] = p
        self._cabecalho(p, "Exportar dados", "Gere arquivos a partir de todo o histórico do banco.")

        grade = ctk.CTkFrame(p, fg_color="transparent")
        grade.pack(fill="x", pady=(0, 14))
        grade.grid_columnconfigure((0, 1), weight=1, uniform="e")

        def bloco(col, emoji, titulo, desc, cor, cmd):
            c = ctk.CTkFrame(grade, fg_color=CARD, corner_radius=16)
            c.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            ctk.CTkLabel(c, text=emoji, font=ctk.CTkFont(size=34)).pack(anchor="w", padx=18, pady=(16, 0))
            ctk.CTkLabel(c, text=titulo, font=ctk.CTkFont(size=17, weight="bold"),
                         text_color=TEXT).pack(anchor="w", padx=18)
            ctk.CTkLabel(c, text=desc, text_color=MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18)
            ctk.CTkButton(c, text=f"Exportar {titulo}", height=38, corner_radius=10, fg_color=cor,
                          hover_color=cor, command=cmd).pack(fill="x", padx=18, pady=16)

        bloco(0, "📊", "Excel", "Planilha .xlsx pronta para análise", GREEN, self.exportar_excel)
        bloco(1, "🧾", "JSON", "Dados estruturados para integrações", ACCENT, self.exportar_json)

        topo = ctk.CTkFrame(p, fg_color="transparent")
        topo.pack(fill="x")
        ctk.CTkLabel(topo, text="🗂️  Exportações salvas", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=TEXT).pack(side="left")
        self.lista_exp = ctk.CTkScrollableFrame(p, fg_color=CARD_ALT, corner_radius=14)
        self.lista_exp.pack(fill="both", expand=True, pady=(8, 0))

    def _atualizar_lista_exportacoes(self):
        for w in self.lista_exp.winfo_children():
            w.destroy()
        arquivos = listar_exportacoes()
        if not arquivos:
            ctk.CTkLabel(self.lista_exp, text="Nenhuma exportação encontrada ainda.",
                         text_color=MUTED).pack(pady=30)
            return
        for arq in arquivos:
            linha = ctk.CTkFrame(self.lista_exp, fg_color=CARD, corner_radius=10)
            linha.pack(fill="x", padx=6, pady=3)
            icone = "📊" if arq.lower().endswith(".xlsx") else "🧾"
            kb = os.path.getsize(arq) / 1024
            ctk.CTkLabel(linha, text=f"{icone}  {os.path.basename(arq)}", text_color=TEXT,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=14, pady=10)
            ctk.CTkButton(linha, text="Carregar", width=80, height=28, corner_radius=8,
                          fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          command=lambda a=arq: self.carregar_arquivo(a)).pack(side="right", padx=12)
            ctk.CTkLabel(linha, text=f"{kb:.1f} KB", text_color=MUTED,
                         font=ctk.CTkFont(size=11)).pack(side="right")

    def _nome_export(self, ext):
        return os.path.join(EXPORT_DIR, f"historico_exportado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}")

    def exportar_excel(self):
        df = obter_df_historico()
        if df.empty:
            self.toast("O banco não possui registros para exportar.", "aviso")
            return
        nome = self._nome_export("xlsx")
        try:
            df.to_excel(nome, index=False)
            self.toast(f"Excel gerado: {os.path.basename(nome)}", "ok")
            self._atualizar_lista_exportacoes()
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao exportar:\n{e}")

    def exportar_json(self):
        df = obter_df_historico()
        if df.empty:
            self.toast("O banco não possui registros para exportar.", "aviso")
            return
        nome = self._nome_export("json")
        with open(nome, "w", encoding="utf-8") as f:
            json.dump(df.to_dict(orient="records"), f, ensure_ascii=False, indent=2)
        self.toast(f"JSON gerado: {os.path.basename(nome)}", "ok")
        self._atualizar_lista_exportacoes()

    # ---------- admin ----------
    def _janela(self, titulo, geom):
        w = ctk.CTkToplevel(self)
        w.title(titulo)
        w.geometry(geom)
        w.configure(fg_color=BG)
        w.transient(self)
        w.after(200, lambda: (w.focus_force(), w.lift()))
        return w

    def abrir_login_admin(self):
        w = self._janela("Login Admin", "360x380")
        w.resizable(False, False)

        ctk.CTkLabel(w, text="🔐", font=ctk.CTkFont(size=44)).pack(pady=(28, 0))
        ctk.CTkLabel(w, text="Acesso restrito", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=TEXT).pack()
        ctk.CTkLabel(w, text="Entre para ver o histórico completo", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(pady=(0, 16))

        ent_u = ctk.CTkEntry(w, placeholder_text="Usuário", width=260, height=40, corner_radius=10)
        ent_u.pack(pady=5)
        ent_s = ctk.CTkEntry(w, placeholder_text="Senha", show="•", width=260, height=40, corner_radius=10)
        ent_s.pack(pady=5)
        lbl_erro = ctk.CTkLabel(w, text="", text_color=RED, font=ctk.CTkFont(size=12))
        lbl_erro.pack(pady=(2, 0))

        def tentar():
            if verificar_login_admin(ent_u.get().strip(), ent_s.get()):
                w.destroy()
                self.abrir_painel_admin()
            else:
                lbl_erro.configure(text="Usuário ou senha incorretos.")

        ent_s.bind("<Return>", lambda e: tentar())
        ctk.CTkButton(w, text="Entrar", width=260, height=40, corner_radius=10, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=14, weight="bold"),
                      command=tentar).pack(pady=10)
        w.after(300, ent_u.focus_set)

    def abrir_painel_admin(self):
        w = self._janela("Painel Admin — Histórico Completo", "980x680")

        topo = ctk.CTkFrame(w, fg_color="transparent")
        topo.pack(fill="x", padx=20, pady=(18, 10))
        ctk.CTkLabel(topo, text="🔐 Painel Admin", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT).pack(side="left")

        res = ResultsView(w, self)
        registros = obter_historico_completo()

        def baixar():
            df = obter_df_historico()
            if df.empty:
                self.toast("O banco não possui registros para baixar.", "aviso")
                return
            caminho = filedialog.asksaveasfilename(
                parent=w, title="Salvar histórico completo como...", defaultextension=".json",
                initialfile=f"historico_completo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                filetypes=[("Arquivo JSON", "*.json")])
            if not caminho:
                return
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(df.to_dict(orient="records"), f, ensure_ascii=False, indent=2)
            self.toast("Histórico completo salvo!", "ok")

        ctk.CTkButton(topo, text="📥  Baixar tudo em JSON", height=36, corner_radius=10,
                      fg_color=GREEN, hover_color=GREEN, command=baixar).pack(side="right")

        res.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        res.set_data(registros)


if __name__ == "__main__":
    App().mainloop()