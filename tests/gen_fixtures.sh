#!/usr/bin/env bash
# 重新生成 tests/fixtures 下的全部测试证书（仅测试夹具用，库本身不依赖 openssl）。
set -euo pipefail
cd "$(dirname "$0")/fixtures"

DAYS_ROOT=3650
DAYS_INT=1825
DAYS_LEAF=825

key() { openssl genrsa -out "$1" 2048 2>/dev/null; }

# ---------- 根 CA ----------
key rootA.key.pem
openssl req -new -x509 -key rootA.key.pem -sha256 -days $DAYS_ROOT \
  -subj "/O=Demo PKI/CN=Root A CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -out rootA.crt.pem

key rootB.key.pem
openssl req -new -x509 -key rootB.key.pem -sha256 -days $DAYS_ROOT \
  -subj "/O=Other PKI/CN=Root B CA (untrusted)" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -out rootB.crt.pem

# ---------- 普通中间 CA（RootA 签发） ----------
key interA.key.pem
openssl req -new -key interA.key.pem -subj "/O=Demo PKI/CN=Intermediate A" -out interA.csr.pem
openssl x509 -req -in interA.csr.pem -CA rootA.crt.pem -CAkey rootA.key.pem \
  -set_serial 1001 -days $DAYS_INT -sha256 \
  -extfile <(printf '%s\n' \
    "basicConstraints=critical,CA:TRUE,pathlen:1" \
    "keyUsage=critical,keyCertSign,cRLSign" \
    "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid") \
  -out interA.crt.pem

# ---------- 已过期的中间 CA（RootA 签发，2020-2021 有效） ----------
key interExp.key.pem
openssl req -new -key interExp.key.pem -subj "/O=Demo PKI/CN=Intermediate Expired" -out interExp.csr.pem
rm -rf cadb && mkdir -p cadb && touch cadb/index.txt && echo 2000 > cadb/serial
cat > cadb/ca.cnf <<'CNF'
[ca]
default_ca = CA_default
[CA_default]
dir = ./cadb
database = $dir/index.txt
new_certs_dir = $dir
serial = $dir/serial
certificate = ./rootA.crt.pem
private_key = ./rootA.key.pem
default_md = sha256
policy = policy_any
x509_extensions = inter_ext
copy_extensions = none
[policy_any]
commonName = supplied
organizationName = optional
[inter_ext]
basicConstraints=critical,CA:TRUE
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
CNF
openssl ca -batch -config cadb/ca.cnf -in interExp.csr.pem \
  -startdate 20200101000000Z -enddate 20210101000000Z -out interExp.crt.pem

# ---------- 带名称约束的中间 CA（RootA 签发） ----------
key interNC.key.pem
openssl req -new -key interNC.key.pem -subj "/O=Demo PKI/CN=Intermediate NameConstraints" -out interNC.csr.pem
cat > interNC.ext.cnf <<'CNF'
basicConstraints=critical,CA:TRUE
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
nameConstraints=critical,permitted;DNS:.example.com,excluded;DNS:bad.example.com,permitted;dirName:nc_dir
[nc_dir]
O=Demo PKI
CNF
openssl x509 -req -in interNC.csr.pem -CA rootA.crt.pem -CAkey rootA.key.pem \
  -set_serial 1002 -days $DAYS_INT -sha256 \
  -extfile interNC.ext.cnf \
  -out interNC.crt.pem

# ---------- 交叉签名中间 CA：同一把钥匙/同一个主体，分别被 RootA 与 RootB 签发 ----------
key interX.key.pem
openssl req -new -key interX.key.pem -subj "/O=Demo PKI/CN=Intermediate X (cross-signed)" -out interX.csr.pem
for R in A B; do
  openssl x509 -req -in interX.csr.pem -CA root$R.crt.pem -CAkey root$R.key.pem \
    -set_serial 110$([ $R = A ] && echo 1 || echo 2) -days $DAYS_INT -sha256 \
    -extfile <(printf '%s\n' \
      "basicConstraints=critical,CA:TRUE" \
      "keyUsage=critical,keyCertSign,cRLSign" \
      "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid") \
    -out interX_by_root$R.crt.pem
done

# ---------- pathlen 链：RootA -> InterP(pathlen:0) -> SubP -> LeafP ----------
key interP.key.pem
openssl req -new -key interP.key.pem -subj "/O=Demo PKI/CN=Intermediate PathLen0" -out interP.csr.pem
openssl x509 -req -in interP.csr.pem -CA rootA.crt.pem -CAkey rootA.key.pem \
  -set_serial 1003 -days $DAYS_INT -sha256 \
  -extfile <(printf '%s\n' \
    "basicConstraints=critical,CA:TRUE,pathlen:0" \
    "keyUsage=critical,keyCertSign,cRLSign" \
    "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid") \
  -out interP.crt.pem

key subP.key.pem
openssl req -new -key subP.key.pem -subj "/O=Demo PKI/CN=Sub CA under PathLen0" -out subP.csr.pem
openssl x509 -req -in subP.csr.pem -CA interP.crt.pem -CAkey interP.key.pem \
  -set_serial 1004 -days $DAYS_INT -sha256 \
  -extfile <(printf '%s\n' \
    "basicConstraints=critical,CA:TRUE" \
    "keyUsage=critical,keyCertSign,cRLSign" \
    "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid") \
  -out subP.crt.pem

# ---------- 终端证书 ----------
issue_leaf() { # name  serial  ca_crt  ca_key  subject  san...
  local name=$1 serial=$2 cacrt=$3 cakey=$4 subj=$5; shift 5
  key "$name.key.pem"
  openssl req -new -key "$name.key.pem" -subj "$subj" -out "$name.csr.pem"
  local san=""; [ $# -gt 0 ] && san="subjectAltName=$(IFS=,; echo "$*")"
  openssl x509 -req -in "$name.csr.pem" -CA "$cacrt" -CAkey "$cakey" \
    -set_serial "$serial" -days $DAYS_LEAF -sha256 \
    -extfile <(printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature,keyEncipherment" \
      "extendedKeyUsage=serverAuth" \
      "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid" $san) \
    -out "$name.crt.pem"
}

issue_leaf leaf     3001 interA.crt.pem   interA.key.pem   "/O=Demo PKI/CN=server.example.com"  "DNS:server.example.com" "DNS:www.example.com"
issue_leaf leafX    3002 interX_by_rootA.crt.pem interX.key.pem "/O=Demo PKI/CN=app.example.com" "DNS:app.example.com"
issue_leaf leafExp  3003 interExp.crt.pem interExp.key.pem "/O=Demo PKI/CN=legacy.example.com"   "DNS:legacy.example.com"
issue_leaf leafNcOK 3004 interNC.crt.pem  interNC.key.pem  "/O=Demo PKI/CN=app.example.com"      "DNS:app.example.com"
issue_leaf leafNcEx 3005 interNC.crt.pem  interNC.key.pem  "/O=Demo PKI/CN=bad.example.com"      "DNS:bad.example.com"
issue_leaf leafNcNp 3006 interNC.crt.pem  interNC.key.pem  "/O=Demo PKI/CN=other.org"            "DNS:other.org"
issue_leaf leafNcDir 3007 interNC.crt.pem interNC.key.pem  "/O=Evil Corp/CN=app.example.com"     "DNS:app.example.com"
issue_leaf leafP    3008 subP.crt.pem     subP.key.pem     "/O=Demo PKI/CN=deep.example.com"     "DNS:deep.example.com"

# ---------- 自签证书（不是信任锚） ----------
key selfsigned.key.pem
openssl req -new -x509 -key selfsigned.key.pem -sha256 -days $DAYS_LEAF \
  -subj "/O=Demo PKI/CN=standalone.example.com" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "subjectAltName=DNS:standalone.example.com" \
  -addext "subjectKeyIdentifier=hash" \
  -out selfsigned.crt.pem

rm -f *.csr.pem *.ext.cnf cadb/ca.cnf
echo "fixtures generated in $(pwd)"
