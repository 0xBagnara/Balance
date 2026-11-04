#!/usr/bin/env python3
"""Consulta saldos públicos BTC das listas do projeto Balance.

Coloque este script em ~/Projects/Puzzle/Balance/consultar_btc.py.
Uso (de dentro da pasta Balance):
    python3 consultar_btc.py
    python3 consultar_btc.py --intervalo 3
    python3 consultar_btc.py --atualizar
    python3 consultar_btc.py --arquivo puzzles_pendentes.txt

Entrada:  Balance/list/**/*.txt e **/*.json.
Saídas:   Balance/resultados/todos.csv, com_saldo.txt, erros.txt, estado.json.
Os caminhos são relativos à pasta do próprio script; não dependem do diretório atual.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from datetime import datetime
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PASTA = Path(__file__).resolve().parent
LISTAS = PASTA / "list"
RESULTADOS = PASTA / "resultados"
ESTADO = RESULTADOS / "estado.json"
TODOS = RESULTADOS / "todos.csv"
COM_SALDO = RESULTADOS / "com_saldo.txt"
ERROS = RESULTADOS / "erros.txt"
SATOSHIS = 100_000_000

# Apenas endereços públicos Bitcoin mainnet (formatos 1..., 3..., bc1...).
# A API valida o checksum, caso um endereço pareça válido, mas não seja.
PADRAO = re.compile(
    r"(?<![A-Za-z0-9])(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|bc1[ac-hj-np-z02-9]{11,87}|BC1[AC-HJ-NP-Z02-9]{11,87})(?![A-Za-z0-9])"
)
CAMPOS = (
    "arquivo_origem", "endereco", "recebidos_btc", "gastos_btc",
    "saldo_btc", "transacoes_confirmadas", "situacao", "consultado_em", "erro",
)


def agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def btc(sats: int) -> str:
    sinal = "-" if sats < 0 else ""
    valor = abs(sats)
    return f"{sinal}{valor // SATOSHIS}.{valor % SATOSHIS:08d}"


def satoshis(valor: str) -> int:
    """Transforma string decimal exata de BTC em inteiro de satoshis."""
    if not re.fullmatch(r"-?\d+(?:\.\d{1,8})?", valor):
        raise ValueError(f"Valor BTC inválido: {valor!r}")
    sinal = -1 if valor.startswith("-") else 1
    parte_inteira, _, frac = valor.lstrip("-").partition(".")
    return sinal * (int(parte_inteira) * SATOSHIS + int(frac.ljust(8, "0")))


def reconhecer(texto: str):
    for match in PADRAO.finditer(texto):
        yield match.group(0)


def percorrer_json(objeto):
    if isinstance(objeto, str):
        yield from reconhecer(objeto)
    elif isinstance(objeto, dict):
        for valor in objeto.values():
            yield from percorrer_json(valor)
    elif isinstance(objeto, list):
        for valor in objeto:
            yield from percorrer_json(valor)


def resolver_arquivo_escolhido(nome: str) -> tuple[Path, Path]:
    """Localiza um TXT/JSON dentro de Balance/list e retorna caminho e nome relativo."""
    informado = Path(nome).expanduser()
    candidatos = [informado] if informado.is_absolute() else [LISTAS / informado, PASTA / informado]
    arquivo = next((c.resolve() for c in candidatos if c.is_file()), None)
    if arquivo is None:
        raise RuntimeError(f"Arquivo não encontrado: {nome} (procure em {LISTAS})")
    try:
        relativo = arquivo.relative_to(LISTAS.resolve())
    except ValueError:
        raise RuntimeError("Use somente arquivos dentro da pasta Balance/list/") from None
    if arquivo.suffix.lower() not in (".txt", ".json"):
        raise RuntimeError("O arquivo precisa ter extensão .txt ou .json")
    return arquivo, relativo


def configurar_resultados(relativo: Path | None):
    """Mantém relatórios de consultas individuais separados dos relatórios gerais."""
    global RESULTADOS, ESTADO, TODOS, COM_SALDO, ERROS
    RESULTADOS = PASTA / "resultados"
    if relativo is not None:
        RESULTADOS = RESULTADOS / "por_arquivo" / relativo
    ESTADO = RESULTADOS / "estado.json"
    TODOS = RESULTADOS / "todos.csv"
    COM_SALDO = RESULTADOS / "com_saldo.txt"
    ERROS = RESULTADOS / "erros.txt"


def carregar_listas(arquivo_escolhido: Path | None = None):
    """Retorna endereço -> lista de origens, quantidade de arquivos e problemas."""
    if not LISTAS.is_dir():
        raise RuntimeError(f"Pasta de listas não encontrada: {LISTAS}")

    arquivos = (
        [arquivo_escolhido] if arquivo_escolhido is not None else sorted(
            (p for p in LISTAS.rglob("*") if p.is_file() and p.suffix.lower() in (".txt", ".json")),
            key=lambda p: str(p).lower(),
        )
    )
    if not arquivos:
        raise RuntimeError(f"Nenhum .txt ou .json encontrado em: {LISTAS}")

    enderecos = {}
    avisos = []
    for arquivo in arquivos:
        origem = str(arquivo.relative_to(LISTAS))
        try:
            conteudo = arquivo.read_text(encoding="utf-8-sig")
            if arquivo.suffix.lower() == ".json":
                encontrados = list(percorrer_json(json.loads(conteudo)))
            else:
                encontrados = []
                for numero, linha in enumerate(conteudo.splitlines(), start=1):
                    texto = linha.split("#", 1)[0].strip()
                    if not texto:
                        continue
                    itens = list(reconhecer(texto))
                    if not itens:
                        avisos.append(f"{origem}:{numero} | Nenhum endereço BTC reconhecido")
                    encontrados.extend(itens)

            if not encontrados:
                avisos.append(f"{origem} | Arquivo sem endereços BTC reconhecidos")
            for endereco in encontrados:
                # Bech32 não diferencia maiúsculas/minúsculas; Base58 diferencia.
                chave = endereco.lower() if endereco.lower().startswith("bc1") else endereco
                enderecos.setdefault(chave, set()).add(origem)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            avisos.append(f"{origem} | Erro de leitura/JSON: {exc}")

    return {e: sorted(origens) for e, origens in enderecos.items()}, len(arquivos), avisos


def salvar_json_atomico(caminho: Path, dados: dict):
    salvar_texto_atomico(caminho, json.dumps(dados, ensure_ascii=False, indent=2) + "\n")


def sincronizar_pasta(caminho: Path):
    descritor = os.open(caminho, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descritor)
    finally:
        os.close(descritor)


def salvar_texto_atomico(caminho: Path, texto: str):
    descritor, nome = tempfile.mkstemp(prefix="." + caminho.name + "-", dir=caminho.parent)
    temporario = Path(nome)
    try:
        with os.fdopen(descritor, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(texto)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        temporario.replace(caminho)
        sincronizar_pasta(caminho.parent)
    finally:
        temporario.unlink(missing_ok=True)


@contextmanager
def bloquear_resultados():
    pasta = PASTA / "resultados"
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / ".balance.lock").open("a+") as arquivo:
        try:
            fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Outra execução do Balance está gravando resultados.") from None
        try:
            arquivo.seek(0)
            arquivo.truncate()
            arquivo.write(str(os.getpid()) + "\n")
            arquivo.flush()
            yield
        finally:
            fcntl.flock(arquivo, fcntl.LOCK_UN)


def validar_linha(linha):
    if not isinstance(linha, dict) or set(linha) != set(CAMPOS):
        raise ValueError("Campos inválidos no histórico")
    if not all(isinstance(v, str) for v in linha.values()):
        raise ValueError("Valores inválidos no histórico")
    if list(reconhecer(linha["endereco"])) != [linha["endereco"]]:
        raise ValueError("Endereço inválido no histórico")
    if linha["situacao"] not in ("COM SALDO", "ZERADO", "ERRO"):
        raise ValueError("Situação inválida no histórico")
    if not linha["arquivo_origem"] or not linha["consultado_em"]:
        raise ValueError("Origem ou data ausente no histórico")
    datetime.fromisoformat(linha["consultado_em"])
    if linha["situacao"] != "ERRO":
        recebido, gasto, saldo = (satoshis(linha[c]) for c in ("recebidos_btc", "gastos_btc", "saldo_btc"))
        if min(recebido, gasto, saldo) < 0 or recebido - gasto != saldo:
            raise ValueError("Saldo inconsistente no histórico")
        if not linha["transacoes_confirmadas"].isdigit():
            raise ValueError("Contagem de transações inválida")
        if (saldo > 0) != (linha["situacao"] == "COM SALDO"):
            raise ValueError("Situação incompatível com saldo")


def abrir_historico(caminho=None):
    registros = {}
    try:
        with (caminho or TODOS).open("r", encoding="utf-8-sig", newline="") as fh:
            leitor = csv.DictReader(fh, strict=True)
            if leitor.fieldnames != list(CAMPOS):
                raise ValueError("Cabeçalho inválido no histórico")
            for linha in leitor:
                validar_linha(linha)
                registros[linha["endereco"]] = linha
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        raise RuntimeError(f"Histórico ilegível ou inválido; arquivos preservados: {exc}") from exc
    return registros


def carregar_estado():
    try:
        estado = json.loads(ESTADO.read_text(encoding="utf-8"))
        if not isinstance(estado, dict) or estado.get("situacao") not in ("em_andamento", "pendencias", "concluido"):
            raise ValueError("Formato de estado inválido")
        if not isinstance(estado.get("assinatura"), str) or not isinstance(estado.get("iniciado_em"), str):
            raise ValueError("Identificação de estado ausente")
        if "versao" in estado:
            if estado["versao"] != 2 or not isinstance(estado.get("registros"), dict):
                raise ValueError("Versão ou registros de checkpoint inválidos")
            if not isinstance(estado.get("api"), str):
                raise ValueError("Fonte do checkpoint ausente")
            for endereco, linha in estado["registros"].items():
                validar_linha(linha)
                if linha["endereco"] != endereco:
                    raise ValueError("Endereço divergente no checkpoint")
        return estado
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Estado ilegível ou inválido; arquivos preservados: {exc}") from exc


def arquivar_resultados():
    arquivos = [RESULTADOS / n for n in ("todos.csv", "estado.json", "com_saldo.txt", "erros.txt", "avisos.txt") if (RESULTADOS / n).is_file()]
    if not arquivos:
        return
    pasta = RESULTADOS / "historico" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    pasta.mkdir(parents=True)
    manifesto = {}
    for origem in arquivos:
        conteudo = origem.read_bytes()
        destino = pasta / origem.name
        with destino.open("xb") as arquivo:
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        resumo = hashlib.sha256(conteudo).hexdigest()
        if hashlib.sha256(destino.read_bytes()).hexdigest() != resumo:
            raise RuntimeError("Falha na verificação do arquivo histórico")
        manifesto[origem.name] = resumo
    salvar_json_atomico(pasta / "manifesto-sha256.json", manifesto)
    sincronizar_pasta(pasta.parent)
    sincronizar_pasta(RESULTADOS)
    print(f"Rodada anterior preservada: {pasta}")


def salvar_csv(registros):
    texto = io.StringIO(newline="")
    escritor = csv.DictWriter(texto, fieldnames=CAMPOS)
    escritor.writeheader()
    escritor.writerows(registros.values())
    salvar_texto_atomico(TODOS, "\ufeff" + texto.getvalue())


def tentar_consulta(endereco: str, api: str, timeout: float, tentativas: int) -> dict:
    url = f"{api.rstrip('/')}/address/{quote(endereco, safe='')}"
    ultimo_erro = None

    for numero in range(1, tentativas + 1):
        try:
            pedido = Request(url, headers={"User-Agent": "Balance-BTC/1.0", "Accept": "application/json"})
            with urlopen(pedido, timeout=timeout) as resposta:
                dados = json.load(resposta)
            stats = dados["chain_stats"]
            recebido = stats["funded_txo_sum"]
            gasto = stats["spent_txo_sum"]
            transacoes = stats["tx_count"]
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (recebido, gasto, transacoes)):
                raise ValueError("Valores inválidos no retorno da API")
            if gasto > recebido:
                raise ValueError("A API retornou gastos maiores que os recebimentos")
            return {"recebido": recebido, "gasto": gasto, "saldo": recebido - gasto, "tx": transacoes}
        except HTTPError as exc:
            ultimo_erro = f"HTTP {exc.code}"
            if exc.code not in (429, 500, 502, 503, 504) or numero == tentativas:
                break
            espera = min(60.0, float(2 ** numero))
            cabecalho = exc.headers.get("Retry-After") if exc.headers else None
            if cabecalho:
                try:
                    espera = min(120.0, max(0.0, float(cabecalho)))
                except ValueError:
                    pass
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            ultimo_erro = f"Falha de conexão: {exc}"
            if numero == tentativas:
                break
            espera = min(60.0, float(2 ** numero))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            ultimo_erro = f"Resposta inesperada: {exc}"
            break
        print(f"   Aviso: {ultimo_erro}, nova tentativa {numero + 1}/{tentativas}...", flush=True)
        time.sleep(espera)

    raise RuntimeError(ultimo_erro or "Erro desconhecido")


def linha_resultado(endereco, origens, dados=None, erro=""):
    if dados is None:
        recebido = gasto = saldo = ""
        transacoes = ""
        situacao = "ERRO"
    else:
        recebido = btc(dados["recebido"])
        gasto = btc(dados["gasto"])
        saldo = btc(dados["saldo"])
        transacoes = str(dados["tx"])
        situacao = "COM SALDO" if dados["saldo"] else "ZERADO"
    return {
        "arquivo_origem": "; ".join(origens),
        "endereco": endereco,
        "recebidos_btc": recebido,
        "gastos_btc": gasto,
        "saldo_btc": saldo,
        "transacoes_confirmadas": transacoes,
        "situacao": situacao,
        "consultado_em": agora(),
        "erro": erro,
    }


def gerar_relatorios(enderecos, registros, avisos, compactar=False):
    validos = [registros[e] for e in enderecos if e in registros]

    if compactar:
        salvar_csv({linha["endereco"]: linha for linha in validos})

    saldos = [linha["endereco"] for linha in validos if linha["situacao"] == "COM SALDO"]
    problemas = [
        f"{linha['endereco']} | {linha['arquivo_origem']} | {linha.get('erro', 'Erro sem detalhes')}"
        for linha in validos if linha["situacao"] == "ERRO"
    ]
    salvar_texto_atomico(COM_SALDO, "\n".join(saldos) + ("\n" if saldos else ""))
    salvar_texto_atomico(ERROS, "\n".join(problemas) + ("\n" if problemas else ""))
    salvar_texto_atomico(RESULTADOS / "avisos.txt", "\n".join(avisos) + ("\n" if avisos else ""))

    ok = [linha for linha in validos if linha["situacao"] != "ERRO"]
    com_saldo = sum(linha["situacao"] == "COM SALDO" for linha in ok)
    soma_recebido = sum(satoshis(linha["recebidos_btc"]) for linha in ok)
    soma_saldo = sum(satoshis(linha["saldo_btc"]) for linha in ok)
    print("\n" + "=" * 70)
    print(f"Endereços únicos: {len(enderecos)} | Consultados com sucesso: {len(ok)}")
    print(f"Com saldo: {com_saldo} | Zerados: {len(ok) - com_saldo} | Erros: {len(problemas)} | Avisos de entrada: {len(avisos)}")
    print(f"Total recebido (endereços consultados): {btc(soma_recebido)} BTC")
    print(f"Saldo confirmado somado:               {btc(soma_saldo)} BTC")
    print(f"Relatórios: {RESULTADOS}")
    print("  todos.csv | com_saldo.txt | erros.txt | avisos.txt")
    print("Obs.: totais apenas confirmados; mempool não incluído.")
    return len(problemas)


def executar():
    parser = argparse.ArgumentParser(description="Consulta saldos em Balance/list (todos os arquivos ou um TXT/JSON específico).")
    parser.add_argument("--arquivo", help="Consulta somente este TXT/JSON dentro de Balance/list (ex.: puzzles_pendentes.txt)")
    parser.add_argument("--intervalo", type=float, default=2.0, help="Segundos entre endereços (padrão: 2)")
    parser.add_argument("--timeout", type=float, default=20.0, help="Tempo máximo por tentativa, em segundos")
    parser.add_argument("--tentativas", type=int, default=3, help="Tentativas para erros temporários (padrão: 3)")
    parser.add_argument("--atualizar", action="store_true", help="Recomeça mesmo que exista consulta interrompida")
    parser.add_argument("--api", default="https://blockstream.info/api", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.intervalo < 0 or args.timeout <= 0 or args.tentativas < 1:
        parser.error("--intervalo deve ser >= 0, --timeout > 0 e --tentativas >= 1")

    arquivo_escolhido = None
    relativo = None
    if args.arquivo:
        arquivo_escolhido, relativo = resolver_arquivo_escolhido(args.arquivo)
    configurar_resultados(relativo)
    with bloquear_resultados():
        return executar_consultas(args, arquivo_escolhido, relativo)


def executar_consultas(args, arquivo_escolhido, relativo):
    enderecos, quantidade_arquivos, avisos = carregar_listas(arquivo_escolhido)
    if not enderecos:
        for aviso in avisos:
            print(f"Aviso: {aviso}", file=sys.stderr)
        raise RuntimeError("Nenhum endereço Bitcoin encontrado nas listas")

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    # Importa resultados antigos sem mover ou alterar os arquivos originais.
    if relativo is not None and not any(RESULTADOS.iterdir()):
        legado = PASTA / "resultados" / "por_arquivo" / relativo.with_suffix("")
        if legado != RESULTADOS and (legado / "todos.csv").is_file():
            registros_legados = abrir_historico(legado / "todos.csv")
            if registros_legados and all(linha["arquivo_origem"] == str(relativo) for linha in registros_legados.values()):
                # Lê e valida o estado antes de copiar qualquer arquivo.
                try:
                    antigo = json.loads((legado / "estado.json").read_text(encoding="utf-8"))
                    if not isinstance(antigo, dict):
                        raise ValueError("Estado legado inválido")
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"Não foi possível importar estado legado: {exc}") from exc
                # CSV primeiro: um estado ausente nunca é tratado como retomável.
                salvar_csv(registros_legados)
                salvar_json_atomico(ESTADO, antigo)
                print(f"Resultados legados copiados de {legado}; originais preservados.")
    assinatura = hashlib.sha256(json.dumps(
        list(enderecos.items()), sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()

    estado = carregar_estado()
    registros_anteriores = estado["registros"] if estado.get("versao") == 2 else abrir_historico()
    if estado and not TODOS.is_file() and estado.get("versao") != 2:
        raise RuntimeError("Estado legado sem CSV; arquivos preservados para recuperação.")
    api = args.api.rstrip("/")
    # Estados antigos não registravam a fonte; somente o padrão original é compatível.
    fonte_anterior = estado.get("api", "https://blockstream.info/api")

    retomar = (
        not args.atualizar
        and estado.get("assinatura") == assinatura
        and estado.get("situacao") in ("em_andamento", "pendencias")
        and fonte_anterior == api
    )
    if retomar:
        registros = {e: linha for e, linha in registros_anteriores.items() if e in enderecos}
        print("Retomando consulta anterior: endereços já consultados serão preservados.")
    else:
        arquivar_resultados()
        registros = {}

    estado = {
        "versao": 2, "assinatura": assinatura, "api": api,
        "situacao": "em_andamento", "iniciado_em": estado["iniciado_em"] if retomar else agora(),
        "atualizado_em": agora(), "registros": registros,
        "total_enderecos": len(enderecos), "avisos_entrada": len(avisos),
    }
    # O checkpoint é a fonte de retomada. Relatórios podem ser regenerados dele.
    salvar_json_atomico(ESTADO, estado)
    salvar_csv(registros)

    lista = list(enderecos)
    pendentes = [e for e in lista if registros.get(e, {}).get("situacao") not in ("ZERADO", "COM SALDO")]
    print(f"\nArquivos: {quantidade_arquivos} | Endereços únicos: {len(lista)}")
    print(f"Já consultados: {len(lista) - len(pendentes)} | A consultar: {len(pendentes)}")
    print(f"Fonte: {LISTAS}")
    print(f"API: {args.api}\n")

    interrompido = False
    try:
        for i, endereco in enumerate(pendentes, start=1):
            try:
                dados = tentar_consulta(endereco, args.api, args.timeout, args.tentativas)
                linha = linha_resultado(endereco, enderecos[endereco], dados=dados)
                print(f"[{i}/{len(pendentes)}] {endereco} | Recebido: {linha['recebidos_btc']} | Saldo: {linha['saldo_btc']} BTC", flush=True)
            except RuntimeError as exc:
                linha = linha_resultado(endereco, enderecos[endereco], erro=str(exc))
                print(f"[{i}/{len(pendentes)}] {endereco} | ERRO: {exc}", flush=True)
            registros[endereco] = linha
            estado["atualizado_em"] = agora()
            salvar_json_atomico(ESTADO, estado)
            salvar_csv(registros)
            if i < len(pendentes) and args.intervalo:
                time.sleep(args.intervalo)
    except KeyboardInterrupt:
        interrompido = True
        # Uma interrupção durante a gravação pode ocorrer antes do replace.
        # Recarrega somente o que foi efetivamente confirmado no checkpoint.
        estado = carregar_estado()
        registros = estado["registros"]
        print("\nInterrompido: resultados já consultados foram preservados.")

    problemas = gerar_relatorios(enderecos, registros, avisos, compactar=True)
    estado.update({
        "situacao": "em_andamento" if interrompido else ("pendencias" if problemas else "concluido"),
        "atualizado_em": agora(), "registros": registros,
    })
    salvar_json_atomico(ESTADO, estado)
    if interrompido:
        print("Para continuar, execute o mesmo comando novamente.")
        return 130
    if problemas:
        print("Algumas entradas falharam. Execute novamente para tentar só as pendentes.")
        return 1
    print("Consulta concluída. Na próxima execução, os saldos serão atualizados.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(executar())
    except (OSError, RuntimeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
