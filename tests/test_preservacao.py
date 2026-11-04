import contextlib
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "consultar_btc.py"
A = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
B = "1BitcoinEaterAddressDontSendf59kuE"


class PreservacaoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="balance-test-")
        self.addCleanup(self.tmp.cleanup)
        spec = importlib.util.spec_from_file_location("balance_test", SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.PASTA = Path(self.tmp.name)
        self.m.LISTAS = self.m.PASTA / "list"
        self.m.LISTAS.mkdir()
        self.lista = self.m.LISTAS / "a.txt"
        self.lista.write_text(A + "\n" + B + "\n")
        self.calls = []

    def consulta(self, endereco, *args):
        self.calls.append(endereco)
        return {"recebido": 10, "gasto": 1, "saldo": 9, "tx": 2}

    def run_cli(self, *args, consulta=None):
        with patch.object(sys, "argv", [str(SCRIPT), "--intervalo", "0", *args]), \
                patch.object(self.m, "tentar_consulta", side_effect=consulta or self.consulta), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.m.executar()

    def estado(self):
        return json.loads(self.m.ESTADO.read_text())

    def arquivos(self):
        return {p.name: p.read_bytes() for p in self.m.RESULTADOS.iterdir() if p.is_file()}

    def test_interrupcao_retoma_so_pendente(self):
        def interromper(endereco, *args):
            if endereco == B:
                raise KeyboardInterrupt
            return self.consulta(endereco)
        self.assertEqual(self.run_cli(consulta=interromper), 130)
        inicio = self.estado()["iniciado_em"]
        self.assertEqual(list(self.estado()["registros"]), [A])
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.calls, [A, B])
        self.assertEqual(self.estado()["iniciado_em"], inicio)

    def test_erro_retenta_sem_reconsultar_sucesso(self):
        def falha(endereco, *args):
            if endereco == B:
                raise RuntimeError("offline")
            return self.consulta(endereco)
        self.assertEqual(self.run_cli(consulta=falha), 1)
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.calls, [A, B])

    def test_avisos_nao_criam_pendencias(self):
        (self.m.LISTAS / "exemplo.txt").write_text("exemplo\n")
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.estado()["situacao"], "concluido")
        self.assertIn("exemplo", (self.m.RESULTADOS / "avisos.txt").read_text())
        self.assertEqual(self.m.ERROS.read_text(), "")

    def conferir_arquivo_anterior(self, anterior):
        pastas = list((self.m.RESULTADOS / "historico").iterdir())
        self.assertEqual(len(pastas), 1)
        manifesto = json.loads((pastas[0] / "manifesto-sha256.json").read_text())
        for nome, resumo in manifesto.items():
            self.assertEqual((pastas[0] / nome).read_bytes(), anterior[nome])
            self.assertEqual(hashlib.sha256(anterior[nome]).hexdigest(), resumo)

    def test_mudar_lista_arquiva_antes_de_reiniciar(self):
        self.run_cli()
        anterior = self.arquivos()
        self.lista.write_text(A + "\n")
        self.assertEqual(self.run_cli(), 0)
        self.conferir_arquivo_anterior(anterior)
        self.assertEqual(list(self.estado()["registros"]), [A])

    def test_atualizar_interrompido_preserva_rodada(self):
        self.run_cli(consulta=lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        anterior = self.arquivos()
        self.assertEqual(self.run_cli("--atualizar"), 0)
        self.conferir_arquivo_anterior(anterior)

    def test_nova_rodada_apos_conclusao_arquiva(self):
        self.run_cli()
        anterior = self.arquivos()
        self.assertEqual(self.run_cli(), 0)
        self.conferir_arquivo_anterior(anterior)
        self.assertEqual(self.calls, [A, B, A, B])

    def test_trocar_api_nao_reutiliza_resultados(self):
        self.run_cli(consulta=lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        anterior = self.arquivos()
        self.run_cli("--api", "https://example.invalid/api")
        self.conferir_arquivo_anterior(anterior)
        self.assertEqual(self.estado()["api"], "https://example.invalid/api")

    def test_checkpoint_recupera_csv_desatualizado(self):
        def interromper(endereco, *args):
            if endereco == B:
                raise KeyboardInterrupt
            return self.consulta(endereco)
        self.run_cli(consulta=interromper)
        self.m.TODOS.write_text("CSV incompleto\n")
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.calls, [A, B])
        self.assertEqual(len(self.m.abrir_historico()), 2)

    def test_estado_corrompido_nao_sobrescreve(self):
        self.run_cli()
        self.m.ESTADO.write_text("{quebrado")
        anterior = self.arquivos()
        with self.assertRaisesRegex(RuntimeError, "Estado ilegível"):
            self.run_cli()
        self.assertEqual(anterior, self.arquivos())

    def test_csv_legado_corrompido_nao_sobrescreve(self):
        self.m.configurar_resultados(None)
        self.m.RESULTADOS.mkdir()
        self.m.TODOS.write_text("cabecalho errado\n")
        anterior = self.arquivos()
        with self.assertRaisesRegex(RuntimeError, "Histórico ilegível"):
            self.run_cli()
        # O arquivo de lock é metadado de exclusão mútua, não histórico.
        self.assertEqual(self.m.TODOS.read_bytes(), anterior["todos.csv"])
        self.assertFalse(self.m.ESTADO.exists())

    def test_lista_vazia_nao_altera_relatorios(self):
        self.run_cli()
        anterior = self.arquivos()
        self.lista.write_text("")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
            self.run_cli()
        self.assertEqual(anterior, self.arquivos())

    def test_txt_json_tem_destinos_diferentes(self):
        self.m.configurar_resultados(Path("igual.txt"))
        primeiro = self.m.RESULTADOS
        self.m.configurar_resultados(Path("igual.json"))
        self.assertNotEqual(primeiro, self.m.RESULTADOS)

    def test_importa_legado_preservando_original(self):
        legado = self.m.PASTA / "resultados" / "por_arquivo" / "a"
        legado.mkdir(parents=True)
        linha = self.m.linha_resultado(A, ["a.txt"], self.consulta(A))
        with (legado / "todos.csv").open("w", newline="") as arquivo:
            w = csv.DictWriter(arquivo, fieldnames=self.m.CAMPOS)
            w.writeheader()
            w.writerow(linha)
        enderecos, _, _ = self.m.carregar_listas(self.lista)
        assinatura = hashlib.sha256(json.dumps(list(enderecos.items()), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        (legado / "estado.json").write_text(json.dumps({"assinatura": assinatura, "situacao": "em_andamento", "iniciado_em": self.m.agora()}))
        antes = {p.name: p.read_bytes() for p in legado.iterdir()}
        self.calls.clear()
        self.assertEqual(self.run_cli("--arquivo", "a.txt"), 0)
        self.assertEqual(self.calls, [B])
        self.assertEqual(antes, {p.name: p.read_bytes() for p in legado.iterdir()})

    def test_bloqueio_entre_processos_e_liberacao(self):
        codigo = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('m', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.PASTA = pathlib.Path(sys.argv[2])
try:
    with m.bloquear_resultados(): pass
except RuntimeError:
    sys.exit(7)
"""
        with self.m.bloquear_resultados():
            r = subprocess.run([sys.executable, "-B", "-c", codigo, str(SCRIPT), str(self.m.PASTA)], capture_output=True)
            self.assertEqual(r.returncode, 7, r.stderr)
        r = subprocess.run([sys.executable, "-B", "-c", codigo, str(SCRIPT), str(self.m.PASTA)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_falha_arquivo_historico_nao_reinicia(self):
        self.run_cli()
        anterior = self.arquivos()
        with patch.object(self.m, "arquivar_resultados", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                self.run_cli("--atualizar")
        self.assertEqual(anterior, self.arquivos())

    def test_interrupcao_antes_do_checkpoint_repete_so_nao_confirmado(self):
        original = self.m.salvar_json_atomico
        def interromper(caminho, dados):
            if caminho == self.m.ESTADO and len(dados.get("registros", {})) == 2:
                raise KeyboardInterrupt
            original(caminho, dados)
        with patch.object(self.m, "salvar_json_atomico", side_effect=interromper):
            self.assertEqual(self.run_cli(), 130)
        self.assertEqual(list(self.estado()["registros"]), [A])
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.calls, [A, B, B])

    def test_satoshis_negativos_exatos(self):
        for valor in (0, 1, -1, 100000001, -100000001):
            self.assertEqual(self.m.satoshis(self.m.btc(valor)), valor)

    def test_falha_csv_apos_checkpoint_nao_repete_confirmado(self):
        original = self.m.salvar_csv
        def falhar(registros):
            if len(registros) == 1:
                raise OSError("falha de escrita do relatório")
            original(registros)
        with patch.object(self.m, "salvar_csv", side_effect=falhar):
            with self.assertRaises(OSError):
                self.run_cli()
        self.assertEqual(list(self.estado()["registros"]), [A])
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.calls, [A, B])


if __name__ == "__main__":
    unittest.main()
