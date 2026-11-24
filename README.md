# 🛍️ PriceWatch

**Monitor de preços do Mercado Livre para desktop.**

O PriceWatch pesquisa produtos no Mercado Livre de forma automática, mostra os resultados em uma interface moderna e guarda tudo em um histórico local. Os dados podem ser exportados para Excel ou JSON.

Feito em **Python** com **CustomTkinter** (interface), **Selenium** (automação do navegador) e **SQLite** (banco de dados local).

---

## ✨ Funcionalidades

### Pesquisa
- **Busca automatizada:** digite o produto e o PriceWatch abre o Chrome, acessa o Mercado Livre e extrai **título, preço e link** dos resultados.
- **Quantidade ajustável:** escolha de **1 a 120 produtos** por busca, com um slider. O site pode retornar menos itens que o pedido.
- **Enter ou botão:** a busca dispara ao clicar em **Pesquisar** ou ao apertar **Enter** no campo de texto.
- **Acompanhamento em tempo real:** barra de progresso e status mostrando cada etapa (abrindo o Chrome, carregando itens, lendo preços, salvando).

### Resultados
- **Cartões de produto** com título, termo pesquisado, data/hora e preço.
- **Abrir e copiar link:** cada cartão tem botões para abrir o anúncio no navegador e copiar o link.
- **Estatísticas automáticas:** total de produtos, menor preço, preço médio e maior preço.
- **Filtro e ordenação:** filtre por título ou termo e ordene por menor preço, maior preço ou ordem alfabética.

### Histórico e exportação
- **Histórico no SQLite:** toda busca é gravada automaticamente em `historico.db`.
- **Histórico sob demanda:** a página **Histórico** carrega todas as buscas já feitas quando você pedir.
- **Exportar para Excel (`.xlsx`) e JSON (`.json`):** os arquivos vão para a pasta `exportacoes/`.
- **Exportações salvas:** a página **Exportar** lista os arquivos gerados e permite carregar qualquer um de volta na tela com um clique.
- **Abrir arquivo externo:** leia qualquer planilha `.xlsx`/`.xls` ou arquivo `.json` no mesmo formato exportado pelo programa.

### Painel Admin
- Acesso protegido por usuário e senha.
- Mostra o **histórico completo** do banco e permite **baixar tudo em JSON**.
- As senhas ficam guardadas com **hash (PBKDF2)**, nunca em texto puro.

### Interface
- Barra lateral de navegação (Pesquisar, Histórico, Exportar, Painel Admin).
- **Modo escuro e modo claro**, com alternância pela barra lateral.
- Avisos rápidos (toasts) no canto da tela e mensagens claras de erro.

---

## 📋 Requisitos

- **Python 3.9** ou superior
- **Google Chrome** instalado (o Selenium controla o navegador para fazer as buscas)
- Conexão com a internet

### Bibliotecas

| Biblioteca | Finalidade |
| :--- | :--- |
| `customtkinter` | Interface gráfica moderna (temas claro/escuro, componentes arredondados) |
| `selenium` | Automação do Chrome para acessar o Mercado Livre e extrair os produtos |
| `pandas` | Manipulação dos dados e geração das exportações |
| `openpyxl` | Leitura e escrita de arquivos Excel (`.xlsx`) |
| `sqlite3` | Banco de dados local (já vem com o Python) |
| `PyInstaller` | Opcional: só para gerar o executável `.exe` |

---

## ⚡ Instalação e execução

1. **Clone ou baixe o projeto** e entre na pasta:
   ```bash
   cd PriceWatch
   ```

2. **(Opcional) Crie um ambiente virtual:**
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Linux / macOS
   ```

3. **Instale as dependências:**
   ```bash
   pip install customtkinter selenium pandas openpyxl
   ```

4. **Execute o aplicativo:**
   ```bash
   python monitor_precos_ctk.py
   ```

> O Selenium 4.6 ou superior baixa e gerencia o driver do Chrome automaticamente, então não é preciso instalar o chromedriver manualmente.

---

## 🔐 Acesso ao Painel Admin

Na primeira execução, o banco cria um administrador padrão:

| Usuário | Senha |
| :--- | :--- |
| `root master` | `root` |

> ⚠️ Esse login serve apenas para testes. Se for distribuir o programa, troque a credencial padrão.

---

## 📂 Estrutura do projeto

```text
📁 PriceWatch/
├── 🐍 monitor_precos_ctk.py     # Código-fonte do aplicativo
├── 🗃️ historico.db              # Banco SQLite (criado automaticamente)
├── 📁 exportacoes/              # Arquivos exportados (criada automaticamente)
│   ├── 📄 historico_exportado_AAAAMMDD_HHMMSS.xlsx
│   └── 📄 historico_exportado_AAAAMMDD_HHMMSS.json
└── 📄 README.md                 # Documentação
```

Após gerar o executável, o PyInstaller também cria as pastas `build/` e `dist/`, além do arquivo `PriceWatch.spec`.

---

## 📦 Gerar o executável (.exe)

Com o PyInstaller instalado (`pip install pyinstaller`), execute na pasta do projeto:

```bash
pyinstaller --noconfirm --onefile --windowed --name PriceWatch --collect-all customtkinter monitor_precos_ctk.py
```

O executável será gerado em:

```text
dist\PriceWatch.exe
```

- `--windowed` esconde o console do terminal.
- `--collect-all customtkinter` inclui os temas e arquivos do CustomTkinter, necessários para o programa abrir corretamente.
- O Google Chrome continua sendo necessário na máquina onde o `.exe` for executado.
- `historico.db` e `exportacoes/` são criados na pasta de onde o programa é executado.

---

## 🛠️ Solução de problemas

| Problema | O que fazer |
| :--- | :--- |
| `session not created` ou o Chrome não abre | Atualize o Chrome e o Selenium (`pip install -U selenium`). |
| Nenhum resultado retornado | O Mercado Livre pode ter mudado o layout da página; tente de novo ou ajuste os seletores CSS em `executar_scraping`. |
| Preço aparece como **N/D** | O preço não foi encontrado no anúncio (por exemplo, produto indisponível). |
| Erro ao exportar Excel | Verifique se o `openpyxl` está instalado: `pip install openpyxl`. |
| A interface antiga aparece | Você está rodando o script antigo. Execute `python monitor_precos_ctk.py`. |

---

## ⚠️ Aviso

O PriceWatch faz leitura automatizada de páginas públicas do Mercado Livre. Use com moderação e respeite os termos de uso do site. O projeto não tem vínculo com o Mercado Livre.