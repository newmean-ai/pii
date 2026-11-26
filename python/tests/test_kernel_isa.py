# Matrix kernels of the native core (issue #3).
#
# An x86-64 build made with GCC or Clang chooses its matrix kernel once per process, at the first call that needs it (a
# model run or build_info): "avx2" (8-wide, multiply then add) where CPUID and XGETBV report AVX2 with the XMM and YMM state
# enabled, else "sse". The fused "avx2+fma" (AVX2 and FMA3) is an opt-in: NEWMEAN_PII_KERNEL=avx2+fma in the environment
# before that first call. NEWMEAN_PII_KERNEL=sse forces the 4-wide kernel; an unknown name, or a kernel the CPU or the
# build lacks, leaves the default. The choice is fixed per process, so every kernel runs in a process of its own here.
#
# Checked:
#   - the choice as a pure function of CPUID leaves 1 and 7 and XCR0 (a truth table; every platform, every build);
#   - build_info names the kernel the process runs, for the default and each NEWMEAN_PII_KERNEL value;
#     NEWMEAN_PII_EXPECT_KERNEL=<name> also asserts the default (CI runners and emulated CPUs of a known kind);
#   - the logits digest of every kernel the process can run equals the pinned digest of its kind: "plain" for every
#     kernel without "+fma" (scalar, sse, neon, avx2), "fused" for every "+fma" kernel (neon+fma, sse+fma, avx2+fma).
#     Digest: SHA-256 of each text's float32 logits (little-endian) over every window the model runs, <s> and </s>
#     included, no padding; then SHA-256 of those hex digests joined. The pins come from the code before this change
#     (the 1.6.1 release, KERNEL_BASE below) on arm64; kernel_refs.py rebuilds that code and prints them;
#   - the texts cover what the kernels split on: every row count modulo 12 (4-row and 6-row tiles), an even and an odd
#     (three or more) number of 8-column key panels in attention, the window boundaries and three texts of 1,000 tokens
#     or more; a text set that misses one fails;
#   - with PII_CLI=<pii_cli built from native/core>, the CLI gives the wheel's digests for the same kernel.
# Models: nano q4; NEWMEAN_PII_KERNEL_TESTS=full adds medium q4e on the same texts and a smoke set on all eight
# size/variant pairs; NEWMEAN_PII_KERNEL_TESTS=smoke checks nano q4 on the smoke set only (emulated CPUs). The local copies (PII_MODELS) or, with NEWMEAN_PII_NETWORK_TESTS=1, downloads from Hugging Face; with
# NEWMEAN_PII_KERNEL_TESTS set (to anything), a model or pin that is missing fails instead of skipping.
# Scripts: python tests/test_kernel_isa.py texts OUT.jsonl [MODEL_DIR] writes the texts as JSON lines (for pii_cli);
# bench_kernels.py measures the kernels.
import hashlib
import json
import os
import platform
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL = re.compile(r"(neon|avx2|sse|vector|scalar)(\+fma)?")
X86_64 = platform.machine().lower() in ("x86_64", "amd64", "x64")
MODE = os.environ.get("NEWMEAN_PII_KERNEL_TESTS", "")
REQUIRED = bool(MODE)
FULL = MODE == "full"
SMOKE = MODE == "smoke"
TAGS = 27
WINDOW, STRIDE = 256, 64  # native/core/src/internal.h: PII_WINDOW, PII_STRIDE

# The code before this change, which produced the pins, is the 1.6.1 release: public main
# 4b63a4c142aab3f35b202fc07254f3a626430beb, KERNEL_BASE in .github/workflows/python-wheels.yml, whose kernel paths
# have the git ids pinned there (its kernel code is that of the commit the pins were first made from; three paths differ
# from it in version text only). Seven arm64 builds of it give the pins, each required (kernel_refs.py --ref KERNEL_BASE
# --check --full --require-builds 7): plain from Apple clang 21 with -DPII_NO_FMA, -DPII_SCALAR and
# -DPII_NO_FMA -DPII_WIDE=0 and from GCC 16 with -DPII_NO_FMA and -DPII_SCALAR, fused from the default build of each
# (neon+fma). x86-64 runners check the same code against them with clang and gcc: the same plain flags (sse, scalar),
# -march=x86-64-v3 -DPII_FMA (sse+fma), and no flags (sse, as the 1.6.1 wheels build it).
# model release -> (size, variant) -> digest of each kind over the texts ("plain", "fused") and over the smoke subset
# ("smoke_plain", "smoke_fused"); 295 texts, 22,665 tokens.
PINS = {
    "1.1.0": {
        ("nano", "q4"): {
            "plain": "cc613c5fb7851f657a18184b09ce95240d3dc1ba1b258768339d96a074e24742",
            "fused": "fd31fd424516c7f62b1c4f658d22b1e67855e38070ff84ff5140436dfeb5bc12",
            "smoke_plain": "17ac65b4c04279f97ec817f388dacf703304db1cbf0a81c62c88ded7a64638a0",
            "smoke_fused": "1fcf98c20020546716e8379b337145d33dde09b9cc99f7e50aeae09a2f0370b8",
        },
        ("nano", "q4e"): {
            "smoke_plain": "e02ec15ba3a64fb0b0fc3cd199fb4a21c2cdc051c92578f0dc6feb8afbce2e90",
            "smoke_fused": "56b971f3a7573255e3a3b077cafb921f2e31b21f4061b509578b76757134b5ee",
        },
        ("small", "q4"): {
            "smoke_plain": "5608ada388180b49a2a84746ea12c43cb9c7bfad93459211285f758595ad894b",
            "smoke_fused": "eb6e01dabd830d9d5d87e56acc67692c5931aff3257864bf48edb558c5399677",
        },
        ("small", "q4e"): {
            "smoke_plain": "ae2928db765ba8aa299ebadcc64b713f6eb769003206eeb3a8b98b1ad4dec52e",
            "smoke_fused": "683133ea43b069f33174998794a07acf34cc120eeb2734f5b7cbe0c16dc1e5a5",
        },
        ("medium", "q4"): {
            "smoke_plain": "40c4bca17e7749ac783620c143aba0aea968b817ad708c48ecd593f8e350b836",
            "smoke_fused": "5966614210be5e5b27c7e3f7b1b284daee5a6d48df7a2affa85ffc937bf224b0",
        },
        ("medium", "q4e"): {
            "plain": "136583f2b0bf271214868978fc0cca8c56c991e907555d535c0805da0b9f56c2",
            "fused": "dddbe08253dbb6e546c6733fb8c1206106b0b8510910e84c8c8a05f7093260e0",
            "smoke_plain": "df54f1a989dfbf6bbbb1f11f51450aa86b8d4e1d1d4c76e464f23c42022f782f",
            "smoke_fused": "41264cb7511295711322b1c6758c93e0a8504b9ea96ca9b2feb0776f17df4ee9",
        },
        ("large", "q4"): {
            "smoke_plain": "11dcc1c29ccc68552a75dcff6b0ba70bbe5b2b9f1abe691a1294519f9571d067",
            "smoke_fused": "4746b78599d92695fbd309c850d77232156f731f45eac3f0c290f8b83425c9a8",
        },
        ("large", "q4e"): {
            "smoke_plain": "41b8108ee0dbfc21b56d1b4c10b43d5b73e868b6935ee2e3cc65d45716bc4cec",
            "smoke_fused": "e4f40b2dc0e427c99652789d0e1e87207d6c6a707e255ad820952dd75467094d",
        },
    }
}
# One text whose plain and fused digests differ (the first text, nano q4): the digests can tell the kinds apart.
WITNESS = {
    "1.1.0": {
        "plain": "4776c69fbcd17070b151852189f33461419577b071fd7237f929ed00bc1ed31f",
        "fused": "5bfd16dd8a58e6bb39c820937be09b0ab14c57c092d162298f457891ef6f714c",
    }
}

# ---------------------------------------------------------------- texts
# name, titled name (PERSON_L2), email, phone, address, ID number, card, IBAN, organisation, username, URL, profile URL
# (URL_L2), date of birth, secret: a few values per language, combined by position so the set is the same on every
# Python version.
_VALUES = {
    "en": [
        ["John Smith", "Emily Johnson", "Michael Brown", "Sarah O'Connor"],
        ["Mr. Smith", "Dr. Patel", "Ms. Garcia"],
        ["john.smith@example.com", "emily.j@mailbox.org", "m.brown+billing@company.co.uk"],
        ["(415) 555-0132", "+1 212 555 0198", "020 7946 0958"],
        ["1600 Pennsylvania Ave NW, Washington, DC 20500", "221B Baker Street, London NW1 6XE", "350 Fifth Avenue, New York, NY 10118"],
        ["123-45-6789", "AB123456C", "987-65-4321"],
        ["4111 1111 1111 1111", "5500 0000 0000 0004", "3400 000000 00009"],
        ["GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00"],
        ["Acme Corporation", "Northwind Traders", "Globex Inc."],
        ["@jsmith_dev", "emily_j88", "mbrown42"],
        ["https://www.example.com/orders/4471", "http://intranet.local/wiki/Onboarding"],
        ["https://www.linkedin.com/in/john-smith-4a1b2c", "https://github.com/emilyj"],
        ["March 14, 1985", "1990-07-02", "12/25/1978"],
        ["sk_live_51HxQ2bKcD9fGh3Jk", "hunter2!Secret", "AKIAIOSFODNN7EXAMPLE"],
    ],
    "ko": [
        ["김민준", "이서연", "박지훈", "최수아"],
        ["김 부장님", "박 교수님", "이 대리"],
        ["minjun.kim@naver.com", "seoyeon@example.co.kr", "jh.park@company.kr"],
        ["010-1234-5678", "02-555-0123", "010 9876 5432"],
        ["서울특별시 강남구 테헤란로 152", "부산광역시 해운대구 센텀중앙로 79", "경기도 성남시 분당구 판교역로 235"],
        ["900101-1234567", "850315-2345678", "120-81-12345"],
        ["9410-1234-5678-9012", "5310 2345 6789 0123"],
        ["국민은행 123456-01-234567", "신한은행 110-234-567890"],
        ["삼성전자", "뉴민주식회사", "서울대학교병원"],
        ["minjun_92", "@seoyeon.lee", "jhpark"],
        ["https://www.example.kr/notice/12", "http://cafe.example.com/board"],
        ["https://www.instagram.com/seoyeon.lee", "https://blog.naver.com/minjun92"],
        ["1990년 1월 1일", "1985.03.15", "92년 7월 2일"],
        ["Pa$$w0rd!2024", "ghp_8fK2mQ9xLr4TzV1bN7cW3eY6uH0aS5dJ"],
    ],
    "ja": [
        ["山田太郎", "佐藤花子", "鈴木一郎"],
        ["田中部長", "高橋先生"],
        ["taro.yamada@example.jp", "hanako@mail.co.jp"],
        ["090-1234-5678", "03-1234-5678"],
        ["東京都千代田区丸の内1-9-1", "大阪府大阪市北区梅田3-1-3"],
        ["1234-5678-9012"],
        ["4980 1234 5678 9012"],
        ["三菱UFJ銀行 普通 1234567"],
        ["株式会社サンプル", "東京大学"],
        ["@taro_y", "hanako_s"],
        ["https://www.example.jp/contact"],
        ["https://twitter.com/taro_y"],
        ["1985年3月14日"],
        ["qwerty-9876-secret"],
    ],
    "zh": [
        ["王伟", "李娜", "张敏"],
        ["王总", "李老师"],
        ["wangwei@example.cn", "lina@163.com"],
        ["138 0013 8000", "021-6123-4567"],
        ["北京市朝阳区建国路88号", "上海市浦东新区世纪大道100号"],
        ["110101199003071234"],
        ["6222 0212 3456 7890 123"],
        ["招商银行 6214 8301 2345 6789"],
        ["阿里巴巴集团", "清华大学"],
        ["wang_wei88"],
        ["https://www.example.cn/about"],
        ["https://weibo.com/lina2020"],
        ["1990年3月7日"],
        ["password: zh9!Lk2#"],
    ],
    "de": [
        ["Hans Müller", "Anna Schmidt", "Jürgen Weiß"],
        ["Herr Müller", "Frau Dr. Schmidt"],
        ["hans.mueller@beispiel.de", "anna.schmidt@firma.de"],
        ["+49 30 1234567", "0171 2345678"],
        ["Unter den Linden 77, 10117 Berlin", "Marienplatz 8, 80331 München"],
        ["T22000129", "12 345 678 901"],
        ["5232 0500 1234 5678"],
        ["DE89 3704 0044 0532 0130 00", "AT61 1904 3002 3457 3201"],
        ["Siemens AG", "Deutsche Bahn"],
        ["hmueller", "@anna_s"],
        ["https://www.beispiel.de/kontakt"],
        ["https://www.xing.com/profile/Anna_Schmidt"],
        ["14.03.1985"],
        ["Kennwort: Sommer2024!"],
    ],
    "fr": [
        ["Jean Dupont", "Marie Lefèvre", "Luc Moreau"],
        ["M. Dupont", "Mme Lefèvre"],
        ["jean.dupont@exemple.fr", "marie.lefevre@societe.fr"],
        ["+33 1 23 45 67 89", "06 12 34 56 78"],
        ["12 rue de Rivoli, 75004 Paris", "5 place Bellecour, 69002 Lyon"],
        ["1 85 03 75 123 456 78"],
        ["4970 1012 3456 7890"],
        ["FR76 3000 6000 0112 3456 7890 189"],
        ["Société Générale", "Université de Lyon"],
        ["jdupont75"],
        ["https://www.exemple.fr/contact"],
        ["https://www.linkedin.com/in/marie-lefevre"],
        ["14 mars 1985"],
        ["mot de passe : Azerty#2024"],
    ],
    "es": [
        ["María García", "José Martínez", "Lucía Fernández"],
        ["Sr. García", "Dra. Martínez"],
        ["maria.garcia@ejemplo.es", "jose.mtz@correo.mx"],
        ["+34 612 345 678", "55 1234 5678"],
        ["Calle Mayor 10, 28013 Madrid", "Avenida Reforma 222, Ciudad de México"],
        ["12345678Z", "GAMA850314HDFRRR09"],
        ["4152 3134 5678 9012"],
        ["ES91 2100 0418 4502 0005 1332"],
        ["Telefónica", "Universidad de Salamanca"],
        ["@mariag", "jose_mtz"],
        ["https://www.ejemplo.es/pedidos"],
        ["https://www.facebook.com/maria.garcia.85"],
        ["14 de marzo de 1985"],
        ["contraseña: Hola!2024"],
    ],
    "pt": [
        ["João Silva", "Ana Souza"],
        ["Sr. Silva", "Dra. Souza"],
        ["joao.silva@exemplo.com.br", "ana.souza@empresa.pt"],
        ["(11) 91234-5678", "+351 912 345 678"],
        ["Avenida Paulista 1000, São Paulo - SP", "Rua Augusta 50, 1100-053 Lisboa"],
        ["123.456.789-09"],
        ["5067 1234 5678 9012"],
        ["PT50 0002 0123 1234 5678 9015 4"],
        ["Petrobras"],
        ["joaosilva"],
        ["https://www.exemplo.com.br/contato"],
        ["https://instagram.com/ana.souza"],
        ["14/03/1985"],
        ["senha: Brasil@123"],
    ],
    "it": [
        ["Giuseppe Rossi", "Giulia Bianchi"],
        ["dott. Rossi", "sig.ra Bianchi"],
        ["giuseppe.rossi@esempio.it"],
        ["+39 06 1234 5678", "333 123 4567"],
        ["Via del Corso 12, 00186 Roma"],
        ["RSSGPP85C14H501Z"],
        ["4023 6001 2345 6789"],
        ["IT60 X054 2811 1010 0000 0123 456"],
        ["Fiat", "Università di Bologna"],
        ["grossi85"],
        ["https://www.esempio.it/contatti"],
        ["https://www.linkedin.com/in/giulia-bianchi"],
        ["14 marzo 1985"],
        ["password: Ciao2024!"],
    ],
    "nl": [
        ["Jan de Vries", "Sanne Jansen"],
        ["meneer De Vries"],
        ["jan.devries@voorbeeld.nl"],
        ["06 12345678", "+31 20 123 4567"],
        ["Damrak 1, 1012 LG Amsterdam"],
        ["123456782"],
        ["5412 7500 1234 5678"],
        ["NL91 ABNA 0417 1643 00"],
        ["Philips"],
        ["sannej"],
        ["https://www.voorbeeld.nl/contact"],
        ["https://twitter.com/sannej"],
        ["14-03-1985"],
        ["wachtwoord: Fiets123!"],
    ],
    "ru": [
        ["Иван Петров", "Анна Смирнова"],
        ["г-н Петров"],
        ["ivan.petrov@example.ru"],
        ["+7 495 123-45-67", "8 (912) 345-67-89"],
        ["Москва, ул. Тверская, д. 7, кв. 12"],
        ["4510 123456"],
        ["2200 1234 5678 9012"],
        ["40817810099910004312"],
        ["Газпром"],
        ["ivan_p"],
        ["https://www.example.ru/kontakty"],
        ["https://vk.com/anna.smirnova"],
        ["14 марта 1985 года"],
        ["пароль: Zima2024!"],
    ],
    "ar": [
        ["محمد أحمد", "فاطمة علي"],
        ["الدكتور أحمد"],
        ["mohamed.ahmed@example.ae"],
        ["+971 50 123 4567"],
        ["شارع الملك فهد، الرياض 12271"],
        ["784-1985-1234567-1"],
        ["4000 1234 5678 9010"],
        ["SA03 8000 0000 6080 1016 7519"],
        ["أرامكو السعودية"],
        ["m_ahmed"],
        ["https://www.example.ae/contact"],
        ["https://twitter.com/fatima_ali"],
        ["14 مارس 1985"],
        ["كلمة المرور: Riyadh#2024"],
    ],
    "hi": [
        ["राहुल शर्मा", "प्रिया पटेल"],
        ["शर्मा जी"],
        ["rahul.sharma@example.in"],
        ["+91 98765 43210"],
        ["12, एमजी रोड, बेंगलुरु 560001"],
        ["1234 5678 9012"],
        ["6521 1234 5678 9012"],
        ["SBIN0001234 खाता 12345678901"],
        ["टाटा मोटर्स"],
        ["rahul_s"],
        ["https://www.example.in/contact"],
        ["https://www.linkedin.com/in/priya-patel"],
        ["14 मार्च 1985"],
        ["पासवर्ड: Delhi@2024"],
    ],
    "vi": [
        ["Nguyễn Văn An", "Trần Thị Bình"],
        ["anh Nguyễn"],
        ["an.nguyen@example.vn"],
        ["0912 345 678", "+84 28 1234 5678"],
        ["123 Lê Lợi, Quận 1, TP. Hồ Chí Minh"],
        ["079085001234"],
        ["9704 1234 5678 9012"],
        ["Vietcombank 0071000123456"],
        ["Vingroup"],
        ["nguyenan85"],
        ["https://www.example.vn/lien-he"],
        ["https://facebook.com/tranthibinh"],
        ["14/03/1985"],
        ["mật khẩu: HaNoi@2024"],
    ],
    "th": [
        ["สมชาย ใจดี", "สุดา รักไทย"],
        ["คุณสมชาย"],
        ["somchai@example.co.th"],
        ["081 234 5678"],
        ["99 ถนนสุขุมวิท แขวงคลองเตย กรุงเทพฯ 10110"],
        ["1 1037 02071 81 1"],
        ["4567 8901 2345 6789"],
        ["กสิกรไทย 123-4-56789-0"],
        ["ปตท."],
        ["somchai_j"],
        ["https://www.example.co.th/contact"],
        ["https://www.facebook.com/suda.rak"],
        ["14 มีนาคม 2528"],
        ["รหัสผ่าน: Bangkok#99"],
    ],
    "tr": [
        ["Mehmet Yılmaz", "Ayşe Kaya"],
        ["Yılmaz Bey"],
        ["mehmet.yilmaz@ornek.com.tr"],
        ["+90 532 123 45 67"],
        ["İstiklal Caddesi No: 10, Beyoğlu, İstanbul"],
        ["12345678950"],
        ["4543 1234 5678 9012"],
        ["TR33 0006 1005 1978 6457 8413 26"],
        ["Türk Hava Yolları"],
        ["ayse_k"],
        ["https://www.ornek.com.tr/iletisim"],
        ["https://www.instagram.com/ayse.kaya"],
        ["14 Mart 1985"],
        ["şifre: Ankara!2024"],
    ],
    "pl": [
        ["Jan Kowalski", "Anna Nowak"],
        ["pan Kowalski"],
        ["jan.kowalski@przyklad.pl"],
        ["+48 601 234 567"],
        ["ul. Marszałkowska 10, 00-590 Warszawa"],
        ["85031412345"],
        ["4246 1234 5678 9012"],
        ["PL61 1090 1014 0000 0712 1981 2874"],
        ["Orlen"],
        ["jkowalski"],
        ["https://www.przyklad.pl/kontakt"],
        ["https://www.linkedin.com/in/anna-nowak"],
        ["14 marca 1985"],
        ["hasło: Wisla2024!"],
    ],
}
_KEYS = ["name", "title", "email", "phone", "addr", "id", "card", "iban", "org", "user", "url", "profile", "dob", "secret"]
_TEMPLATES = {
    "en": [
        "Hi, I'm {name}. You can reach me at {email} or {phone}.",
        "Please ship the order to {addr}. Contact: {name}, {phone}.",
        "Patient {name}, born {dob}, SSN {id}, was admitted yesterday.",
        "Card {card} was charged twice; please refund to IBAN {iban}.",
        "{title} from {org} will join the call. Profile: {profile}",
        "Log in as {user} with password {secret} at {url}",
        "Meeting notes: nothing personal here, just the Q3 roadmap and the budget review for next year.",
        "Thanks {name}! 🎉 I forwarded your CV to {org} 👍 ({email})",
    ],
    "ko": [
        "안녕하세요, 저는 {name}입니다. 연락처는 {phone}, 메일은 {email}입니다.",
        "{addr}로 배송해 주세요. 받는 사람 {name}님께.",
        "주민등록번호 {id}, 생년월일 {dob}인 {name} 님의 서류입니다.",
        "{org} {title}께 전달 부탁드립니다. 프로필: {profile}",
        "카드 {card} 결제가 취소되었고 {iban} 계좌로 환불됩니다.",
        "아이디 {user}, 비밀번호 {secret}로 {url}에 접속하세요.",
        "오늘 회의는 3층 대회의실에서 오후 두 시에 시작합니다. 자료는 미리 읽어 오세요.",
    ],
    "ja": [
        "私は{name}です。電話番号は{phone}、メールは{email}です。",
        "住所は{addr}です。{org}の{title}に連絡してください。",
        "カード番号{card}、口座{iban}、生年月日{dob}。",
        "ユーザー名{user}、パスワード{secret}で{url}にログインしてください。プロフィール: {profile}",
    ],
    "zh": [
        "我叫{name}，电话{phone}，邮箱{email}。",
        "请寄到{addr}。身份证号{id}，出生日期{dob}。",
        "{org}的{title}的银行卡号是{card}，账户{iban}。",
        "用户名{user}，{secret}，网址{url}，主页{profile}。",
    ],
    "de": [
        "Mein Name ist {name}, Telefon {phone}, E-Mail {email}.",
        "Bitte überweisen Sie den Betrag auf IBAN {iban}; Karte {card}.",
        "{title} von {org} wohnt in {addr} und ist am {dob} geboren.",
        "Benutzer {user}, {secret}, Profil {profile}, Seite {url}, Ausweis {id}.",
    ],
    "fr": [
        "Je m'appelle {name}, né le {dob}. Mon courriel est {email}.",
        "Envoyez le colis au {addr}, téléphone {phone}.",
        "{title} de {org} : carte {card}, IBAN {iban}, sécurité sociale {id}.",
        "Identifiant {user}, {secret}, site {url}, profil {profile}.",
    ],
    "es": [
        "Me llamo {name} y vivo en {addr}. Mi teléfono es {phone}.",
        "{title} de {org}, correo {email}, nacida el {dob}, DNI {id}.",
        "Tarjeta {card}, cuenta {iban}, usuario {user}, {secret}.",
        "Mi perfil es {profile} y la tienda es {url}.",
    ],
    "pt": [
        "Meu nome é {name}, CPF {id}, e-mail {email}, telefone {phone}.",
        "{title} da {org} mora na {addr} e nasceu em {dob}.",
        "Cartão {card}, IBAN {iban}, usuário {user}, {secret}, {url}, {profile}.",
    ],
    "it": [
        "Sono {name}, il mio numero è {phone} e la mia email {email}.",
        "{title} di {org} abita in {addr}, nato il {dob}, codice fiscale {id}.",
        "Carta {card}, IBAN {iban}, utente {user}, {secret}, {url}, {profile}.",
    ],
    "nl": [
        "Mijn naam is {name}, IBAN {iban}, telefoon {phone}.",
        "{title} van {org} woont op {addr}, geboren {dob}, BSN {id}.",
        "E-mail {email}, kaart {card}, gebruiker {user}, {secret}, {url}, {profile}.",
    ],
    "ru": [
        "Меня зовут {name}, мой телефон {phone}, почта {email}.",
        "{title} из компании {org} живёт по адресу {addr}, родился {dob}, паспорт {id}.",
        "Карта {card}, счёт {iban}, логин {user}, {secret}, {url}, {profile}.",
    ],
    "ar": [
        "اسمي {name} ورقم هاتفي {phone} وبريدي {email}.",
        "{title} من {org} يسكن في {addr}، تاريخ الميلاد {dob}، الهوية {id}.",
        "البطاقة {card}، الحساب {iban}، المستخدم {user}، {secret}، {url}، {profile}.",
    ],
    "hi": [
        "मेरा नाम {name} है, मेरा ईमेल {email} और फ़ोन {phone} है।",
        "{title} {org} में काम करते हैं, पता {addr}, जन्मतिथि {dob}, आधार {id}।",
        "कार्ड {card}, खाता {iban}, यूज़र {user}, {secret}, {url}, {profile}।",
    ],
    "vi": [
        "Tôi là {name}, số điện thoại {phone}, email {email}.",
        "{title} ở {org}, địa chỉ {addr}, sinh ngày {dob}, CCCD {id}.",
        "Thẻ {card}, tài khoản {iban}, tên đăng nhập {user}, {secret}, {url}, {profile}.",
    ],
    "th": [
        "ฉันชื่อ{name} โทร {phone} อีเมล {email}",
        "{title}จาก{org} อยู่ที่ {addr} เกิดวันที่ {dob} เลขบัตรประชาชน {id}",
        "บัตร {card} บัญชี {iban} ผู้ใช้ {user} {secret} {url} {profile}",
    ],
    "tr": [
        "Adım {name}, e-postam {email}, telefonum {phone}.",
        "{title} {org} çalışanı, adresi {addr}, doğum tarihi {dob}, TC kimlik {id}.",
        "Kart {card}, IBAN {iban}, kullanıcı {user}, {secret}, {url}, {profile}.",
    ],
    "pl": [
        "Nazywam się {name}, mój numer to {phone}, e-mail {email}.",
        "{title} z firmy {org} mieszka przy {addr}, urodzony {dob}, PESEL {id}.",
        "Karta {card}, konto {iban}, login {user}, {secret}, {url}, {profile}.",
    ],
}
_VARIANTS = 4  # value combinations per template


def make_base_texts():
    """About 260 texts in 17 languages with every label, then 3 texts of more than 1,000 tokens (several windows)."""
    texts = []
    for lang, templates in _TEMPLATES.items():
        values = _VALUES[lang]
        for t, template in enumerate(templates):
            for v in range(_VARIANTS):
                fill = {key: values[k][(t + v * (k + 1)) % len(values[k])] for k, key in enumerate(_KEYS)}
                texts.append(template.format(**fill))
    texts.append(" ".join(texts[i] for i in range(0, len(texts), 3)))
    texts.append("\n".join(texts[i] for i in range(1, len(texts) - 1, 5)))
    texts.append(" | ".join(texts[i] for i in range(2, 120, 2)))
    return texts


# Texts of exactly these token counts: 1 to 24 (every row count of a short text, 1 to 4 key panels) and the window
# boundaries (254 tokens fill one window, 255 need two; 444 fill two, 445 need three).
FIT_TOKENS = list(range(1, 25)) + [253, 254, 255, 256, 443, 444, 445, 446]
_PAD = [" Kim", " 7", " Seoul", " x"]  # one token each after a space


def _fit(base, tokenize, n, start):
    """A text of exactly n tokens: whole texts of base from index start while they fit, then one-token words."""
    out = ""
    i = start
    while True:
        t = base[i % len(base)]
        cand = f"{out} {t}" if out else t
        if len(tokenize(cand)) > n:
            break
        out, i = cand, i + 1
    count = len(tokenize(out)) if out else 0
    while count < n:
        for w in _PAD:
            cand = out + w if out else w.strip()
            c = len(tokenize(cand))
            if c == count + 1:
                out, count = cand, c
                break
        else:
            raise AssertionError(f"cannot pad a text to {n} tokens")
    return out


def make_texts(tokenize):
    """TEXTS: the base texts, then one text of each FIT_TOKENS count (built with the model's tokenizer, which every size
    of a model release shares)."""
    base = make_base_texts()
    return base + [_fit(base, tokenize, n, 7 * k) for k, n in enumerate(FIT_TOKENS)]


def smoke_indices(texts):
    """SMOKE, the texts of the eight-pair smoke test: every 25th base text, short fitted texts and two window boundaries."""
    nb = len(texts) - len(FIT_TOKENS)
    fit = {n: nb + k for k, n in enumerate(FIT_TOKENS)}
    return list(range(0, nb - 3, 25)) + [nb - 1] + [fit[n] for n in (1, 5, 6, 17, 24, 254, 255)]


def windows(n):
    """Token counts of the windows the model runs on a text of n tokens (api.c: model_spans), without <s> and </s>."""
    win = WINDOW - 2
    out, s = [], 0
    limit = n - STRIDE if n > STRIDE + 1 else 1
    while n and (s == 0 or (n > win and s < limit)):
        out.append(min(win, n - s))
        if n <= win:
            break
        s += win - STRIDE
    return out


def coverage_gaps(token_counts):
    """What the texts fail to cover of the kernels' splits (empty when they cover it all)."""
    rows = [w + 2 for n in token_counts for w in windows(n)]  # rows of each model run, <s> and </s> included
    gaps = []
    missing = sorted(set(range(12)) - {r % 12 for r in rows})
    if missing:
        gaps.append(f"row counts modulo 12: {missing}")
    panels = {-(-r // 8) for r in rows}  # 8-column key panels of the attention scores
    if not any(p % 2 == 0 for p in panels):
        gaps.append("an even number of key panels")
    if not any(p % 2 == 1 and p >= 3 for p in panels):
        gaps.append("an odd number of key panels, three or more")
    for n in (254, 255, 444, 445):
        if n not in token_counts:
            gaps.append(f"a text of {n} tokens")
    if sum(n >= 1000 for n in token_counts) < 3:
        gaps.append("three texts of 1,000 tokens or more")
    return gaps


def overall(digests):
    """The digest of a text set: SHA-256 of the per-text hex digests joined (as pii_cli logits prints it)."""
    return hashlib.sha256("".join(digests).encode("ascii")).hexdigest()


def kernel_name(kernel_id, build):
    """Name of a pii_kernel_select result in a build with these bits."""
    return {1: "sse+fma" if build & 1 else "sse", 2: "avx2", 3: "avx2+fma"}[kernel_id]


# ---------------------------------------------------------------- workers (one process per kernel)
def run_worker(model_dir, size, variant, texts_path):
    """Logits digests of the texts in this process; the kernel is whatever NEWMEAN_PII_KERNEL makes it."""
    from newmean_pii import Redactor, _core

    with open(texts_path, encoding="utf-8") as f:
        texts = json.load(f)
    r = Redactor(size, variant, model_dir=model_dir)
    digests, tokens = [], []
    for t in texts:
        lg = r._model._logits(t)
        n = len(r.tokenize(t))
        expect = sum(w + 2 for w in windows(n)) * TAGS * 4
        if len(lg) != expect:
            raise SystemExit(f"{len(lg)} bytes of logits for {n} tokens, expected {expect}")
        digests.append(hashlib.sha256(lg).hexdigest())
        tokens.append(n)
    return {"build": _core.build_info(), "inputs": list(_core._kernel_inputs()), "digests": digests,
            "sha256": overall(digests), "tokens": tokens}


def _env(request):
    env = dict(os.environ)
    env.pop("NEWMEAN_PII_KERNEL", None)
    if request is not None:
        env["NEWMEAN_PII_KERNEL"] = request
    return env


def worker(request, model_dir, size, variant, texts_path):
    """run_worker in a new process with NEWMEAN_PII_KERNEL=request (None: unset)."""
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", str(model_dir), size, variant, texts_path],
                       env=_env(request), capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def probe(request):
    """build_info and _kernel_inputs of a new process with NEWMEAN_PII_KERNEL=request."""
    code = "import json; from newmean_pii import _core; print(json.dumps([_core.build_info(), list(_core._kernel_inputs())]))"
    p = subprocess.run([sys.executable, "-c", code], env=_env(request), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    build, inputs = json.loads(p.stdout.strip().splitlines()[-1])
    return build, inputs


def expected_build(inputs, request, default_build):
    """The kernel a process with these CPU values should run for request (a single-kernel build: its one kernel)."""
    from newmean_pii import _core

    if inputs[4] < 0:
        return default_build
    return kernel_name(_core._kernel_select(*inputs[:4], request, inputs[4]), inputs[4])


def kernel_requests():
    """NEWMEAN_PII_KERNEL values that reach every kernel this process can run, the default (None) first."""
    default, inputs = probe(None)
    reqs, seen = [None], {default}
    for req in ("sse", "sse+fma", "avx2", "avx2+fma"):
        name = expected_build(inputs, req, default)
        if name not in seen:
            seen.add(name)
            reqs.append(req)
    return reqs


# ---------------------------------------------------------------- fixtures
def _missing(msg):
    if REQUIRED:
        pytest.fail(msg)
    pytest.skip(msg)


@pytest.fixture(scope="session")
def models(tmp_path_factory):
    """(size, variant) -> folder of a pinned model of this release: the local copy, else a download."""
    from conftest import has_model, model_dir

    cache = tmp_path_factory.mktemp("kernel-models")
    found = {}

    def get(size, variant):
        if (size, variant) not in found:
            if has_model(size, variant):
                found[size, variant] = str(model_dir(size))
            elif os.environ.get("NEWMEAN_PII_NETWORK_TESTS"):
                from newmean_pii import Redactor

                r = Redactor(size, variant, cache_dir=cache)
                found[size, variant] = os.path.dirname(r.info()["files"]["tokenizer"]["local_path"])
            else:
                _missing(f"no local {size} {variant} (PII_MODELS); NEWMEAN_PII_NETWORK_TESTS=1 downloads it")
        return found[size, variant]

    return get


@pytest.fixture(scope="session")
def texts(models, tmp_path_factory):
    """(texts, token counts, path of the texts as JSON, path as JSON lines)."""
    from newmean_pii import Redactor

    r = Redactor("nano", "q4", model_dir=models("nano", "q4"))
    t = make_texts(r.tokenize)
    d = tmp_path_factory.mktemp("kernel-texts")
    path, lines = str(d / "texts.json"), str(d / "texts.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False)
    with open(lines, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({"text": x}, ensure_ascii=False) + "\n" for x in t)
    return t, [len(r.tokenize(x)) for x in t], path, lines


def _pins(pair):
    from newmean_pii import MODEL_VERSION

    pins = PINS.get(MODEL_VERSION, {}).get(pair)
    if not pins:
        _missing(f"no pinned logits for {pair} of model release {MODEL_VERSION}")
    return pins


def _subset(texts, indices, tmp_path):
    path = str(tmp_path / "subset.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([texts[i] for i in indices], f, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- tests
def oracle(max_leaf, ecx, ebx, xcr0, request, build):
    """The kernel choice as the criteria state it (1 = the 4-wide kernel, 2 = "avx2", 3 = "avx2+fma")."""
    fused_build = bool(build & 1)
    avx = max_leaf >= 1 and ecx >> 27 & 1 and ecx >> 28 & 1 and xcr0 & 6 == 6  # OSXSAVE, AVX, XMM and YMM state
    avx2 = bool(avx and max_leaf >= 7 and ebx >> 5 & 1)
    fma = avx2 and bool(ecx >> 12 & 1) and not build & 2
    default = (3 if fma else 1) if fused_build else (2 if avx2 else 1)
    if request == ("sse+fma" if fused_build else "sse"):
        return 1
    if request == "avx2" and avx2:
        return 2
    if request == "avx2+fma" and fma:
        return 3
    return default


OSX, AVX, FMA, AVX2 = 1 << 27, 1 << 28, 1 << 12, 1 << 5


def test_kernel_choice_truth_table():
    from newmean_pii import _core

    sel = _core._kernel_select
    # named CPUs (default build): Nehalem, Sandy Bridge, Haswell and later, AVX2 without FMA3 (some virtual machines),
    # AMD Piledriver (FMA3 without AVX2), an operating system that does not save the YMM registers
    assert sel(0xB, 0x00980000, 0, 0, None, 0) == 1
    assert sel(0xD, OSX | AVX, 0, 7, "avx2", 0) == 1
    assert sel(0x16, OSX | AVX | FMA, AVX2, 7, None, 0) == 2
    assert sel(0x16, OSX | AVX | FMA, AVX2, 7, "avx2+fma", 0) == 3
    assert sel(0x16, OSX | AVX, AVX2, 7, "avx2+fma", 0) == 2
    assert sel(0xD, OSX | AVX | FMA, 0, 7, "avx2+fma", 0) == 1
    assert sel(0x16, OSX | AVX | FMA, AVX2, 3, None, 0) == 1
    # every combination against the oracle: the highest leaf, the leaf 1 bits with noise, leaf 7, XCR0, requests, builds
    n = 0
    for max_leaf in (0, 1, 6, 7, 0xD, 0x20):
        for bits in range(8):
            for noise in (0, 0x00980201):  # SSE3, SSSE3, SSE4.1, SSE4.2 and others
                ecx = noise | (OSX if bits & 1 else 0) | (AVX if bits & 2 else 0) | (FMA if bits & 4 else 0)
                for ebx in (0, AVX2, AVX2 | 0x108, 0x108):
                    for xcr0 in (0, 1, 3, 5, 6, 7, 0xE7):
                        for request in (None, "", "sse", "sse+fma", "avx2", "avx2+fma", "AVX2", "avx2 ", "neon"):
                            for build in (0, 1, 2):
                                assert sel(max_leaf, ecx, ebx, xcr0, request, build) == oracle(
                                    max_leaf, ecx, ebx, xcr0, request, build), (max_leaf, ecx, ebx, xcr0, request, build)
                                n += 1
    assert n == 6 * 8 * 2 * 4 * 7 * 9 * 3


def test_build_info_names_the_executed_kernel():
    from newmean_pii import _core

    default, inputs = probe(None)
    assert KERNEL.fullmatch(default), default
    assert _core.build_info() == probe(os.environ.get("NEWMEAN_PII_KERNEL"))[0]  # this process, as its environment says
    if not X86_64:
        assert inputs == [0, 0, 0, 0, -1], inputs  # one kernel, fixed when compiled
    if inputs[4] >= 0:  # a build that chooses at run time: XGETBV only with OSXSAVE
        assert inputs[1] >> 27 & 1 or inputs[3] == 0, inputs
    expect = os.environ.get("NEWMEAN_PII_EXPECT_KERNEL")
    if expect:
        assert default == expect, (default, inputs)
    if inputs[4] >= 0:
        assert default == expected_build(inputs, None, default), inputs
        assert default != "avx2+fma" or inputs[4] & 1, "the fused kernel is an opt-in"
    for request in ("sse", "sse+fma", "avx2", "avx2+fma", "no-such-kernel", ""):
        build, got_inputs = probe(request)
        assert got_inputs == inputs
        assert build == expected_build(inputs, request, default), (request, build, inputs)
    print(f"\nkernel: {default}; CPUID max leaf {inputs[0]:#x}, leaf 1 ECX {inputs[1]:#010x}, leaf 7 EBX {inputs[2]:#010x}, "
          f"XCR0 {inputs[3]:#x}, build bits {inputs[4]} ({platform.machine()}, {platform.system()})")


def test_texts_cover_the_kernel_splits(texts):
    _, tokens, _, _ = texts
    assert coverage_gaps(tokens) == []
    assert all(tokens[-len(FIT_TOKENS) + k] == n for k, n in enumerate(FIT_TOKENS))


def _check_runs(pair, pins, texts_path, tokens, model_dir, reqs, witness=None):
    builds = {}
    for req in reqs:
        res = worker(req, model_dir, pair[0], pair[1], texts_path)
        assert res["tokens"] == tokens
        kind = "fused" if res["build"].endswith("+fma") else "plain"
        assert res["sha256"] == pins[kind], (pair, req, res["build"], kind)
        if witness:
            assert res["digests"][0] == witness[kind], (req, res["build"])
        builds[res["build"]] = res["sha256"]
    return builds


@pytest.mark.parametrize("pair", [("nano", "q4")] + ([("medium", "q4e")] if FULL else []), ids="-".join)
def test_logits_match_the_pins(pair, models, texts, tmp_path):
    from newmean_pii import MODEL_VERSION

    if sys.byteorder != "little":
        pytest.skip("the pins are of little-endian floats")
    all_texts, tokens, path, _ = texts
    pins = _pins(pair)
    witness = WITNESS.get(MODEL_VERSION) if pair == ("nano", "q4") else None
    if witness:
        assert witness["plain"] != witness["fused"]
    if SMOKE:  # the smoke subset (its first text is the witness too)
        idx = smoke_indices(all_texts)
        path, tokens = _subset(all_texts, idx, tmp_path), [tokens[i] for i in idx]
        all_texts, pins = [all_texts[i] for i in idx], {k: pins["smoke_" + k] for k in ("plain", "fused")}
    builds = _check_runs(pair, pins, path, tokens, models(*pair), kernel_requests(), witness)
    print(f"\n{pair[0]} {pair[1]}: {len(all_texts)} texts, {sum(tokens)} tokens; " +
          ", ".join(f"{b} {d[:12]}" for b, d in builds.items()))


@pytest.mark.skipif(not FULL, reason="NEWMEAN_PII_KERNEL_TESTS=full")
@pytest.mark.parametrize("pair", [(s, v) for s in ("nano", "small", "medium", "large") for v in ("q4", "q4e")], ids="-".join)
def test_smoke_every_model(pair, models, texts, tmp_path):
    all_texts, tokens, _, _ = texts
    pins = _pins(pair)
    idx = smoke_indices(all_texts)
    path = _subset(all_texts, idx, tmp_path)
    _check_runs(pair, {k: pins["smoke_" + k] for k in ("plain", "fused")}, path, [tokens[i] for i in idx], models(*pair),
                kernel_requests())


def test_cli_gives_the_wheel_digests(models, texts):
    cli = os.environ.get("PII_CLI")
    if not cli:
        pytest.skip("PII_CLI=<pii_cli built from native/core> compares the CLI too")
    _, _, _, lines = texts
    d = models("nano", "q4")
    pins = _pins(("nano", "q4"))
    for req in kernel_requests():
        with open(lines, "rb") as f:
            p = subprocess.run([cli, "logits", os.path.join(d, "onnx", "model_q4.onnx"), os.path.join(d, "tokenizer.web.json")],
                               stdin=f, env=_env(req), capture_output=True)
        assert p.returncode == 0, p.stderr[-3000:]
        last = json.loads(p.stdout.decode().strip().splitlines()[-1])
        kind = "fused" if last["build"].endswith("+fma") else "plain"
        assert last["sha256"] == pins[kind], (req, last)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        print(json.dumps(run_worker(*sys.argv[2:6])))
    elif len(sys.argv) >= 3 and sys.argv[1] == "texts":  # texts OUT.jsonl [MODEL_DIR of any size]
        sys.path.insert(0, HERE)
        from conftest import model_dir
        from newmean_pii import Redactor

        r = Redactor("nano", "q4", model_dir=sys.argv[3] if len(sys.argv) > 3 else model_dir("nano"))
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            f.writelines(json.dumps({"text": x}, ensure_ascii=False) + "\n" for x in make_texts(r.tokenize))
    else:
        sys.exit("usage: test_kernel_isa.py texts OUT.jsonl [MODEL_DIR] (the tests run under pytest)")
