#!/usr/bin/env bash
# Generates the certificate fixtures used by test_certchain.py.
# openssl is used ONLY to mint test certificates; the library itself is
# pure Python standard library.
set -euo pipefail
cd "$(dirname "$0")"
FX=fixtures
rm -rf "$FX"
mkdir -p "$FX"
cd "$FX"

ORG="O=CertChain Lab"
D0=20250101000000Z    # notBefore for normal certs
D1=20350101000000Z    # notAfter  for normal certs
DR1=20450101000000Z   # notAfter  for roots

write_cnf() { # $1 = ca dir
cat > "$1/openssl.cnf" <<CNF
[ ca ]
default_ca = CA_default
[ CA_default ]
dir             = $1
database        = \$dir/index.txt
new_certs_dir   = \$dir
serial          = \$dir/serial
default_md      = sha256
policy          = policy_any
unique_subject  = no
[ policy_any ]
commonName              = supplied
organizationName        = optional
countryName             = optional
[ v3_root ]
basicConstraints        = critical, CA:true
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
[ v3_inter ]
basicConstraints        = critical, CA:true, pathlen:1
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
[ v3_inter_p0 ]
basicConstraints        = critical, CA:true, pathlen:0
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
[ v3_nc_root ]
basicConstraints        = critical, CA:true
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
nameConstraints         = critical, permitted;DNS:example.com, excluded;DNS:bad.example.com
[ v3_leaf ]
basicConstraints        = critical, CA:false
keyUsage                = critical, digitalSignature, keyEncipherment
extendedKeyUsage        = serverAuth
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
[ v3_leaf_good ]
basicConstraints        = critical, CA:false
keyUsage                = critical, digitalSignature, keyEncipherment
extendedKeyUsage        = serverAuth
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
subjectAltName          = DNS:www.example.com
[ v3_leaf_outside ]
basicConstraints        = critical, CA:false
keyUsage                = critical, digitalSignature, keyEncipherment
extendedKeyUsage        = serverAuth
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
subjectAltName          = DNS:evil.com
[ v3_leaf_excluded ]
basicConstraints        = critical, CA:false
keyUsage                = critical, digitalSignature, keyEncipherment
extendedKeyUsage        = serverAuth
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always
subjectAltName          = DNS:bad.example.com
CNF
}

new_ca_dir() { mkdir -p "$1"; ( cd "$1" && touch index.txt && echo 1000 > serial ); write_cnf "$1"; }
rsa_key()     { openssl genrsa -out "$1" 2048 2>/dev/null; }
csr()         { openssl req -new -key "$1" -out "$2" -subj "/CN=$3/$ORG"; }

# issue $1=ca_dir $2=ca_key $3=ca_crt $4=csr $5=out $6=ext $7=start $8=end
issue() {
  openssl ca -batch -config "$1/openssl.cnf" -keyfile "$2" -cert "$3" \
    -in "$4" -out "$5" -extensions "$6" -startdate "$7" -enddate "$8"
}
selfsign() {
  openssl ca -batch -selfsign -config "$1/openssl.cnf" -keyfile "$2" \
    -in "$3" -out "$4" -extensions "$5" -startdate "$6" -enddate "$7"
}

# ---------------------------------------------------------------- roots ---
for r in root-a root-b root-nc; do
  new_ca_dir "$r-ca"; rsa_key "$r.key"
done
csr root-a.key  root-a.csr  "Root A"
csr root-b.key  root-b.csr  "Root B"
csr root-nc.key root-nc.csr "Root NC"
selfsign root-a-ca  root-a.key  root-a.csr  root-a.crt  v3_root    $D0 $DR1
selfsign root-b-ca  root-b.key  root-b.csr  root-b.crt  v3_root    $D0 $DR1
selfsign root-nc-ca root-nc.key root-nc.csr root-nc.crt v3_nc_root $D0 $DR1

# ---------------------------------------------------------- intermediates -
new_ca_dir int-ca1-ca
rsa_key int-ca1.key; csr int-ca1.key int-ca1.csr "Intermediate CA 1"
issue root-a-ca root-a.key root-a.crt int-ca1.csr int-ca1.crt v3_inter $D0 $D1

# cross-signed intermediate: ONE key and subject, signed by Root A and Root B
rsa_key int-x.key; csr int-x.key int-x.csr "Intermediate X"
issue root-a-ca root-a.key root-a.crt int-x.csr int-x-by-a.crt v3_inter $D0 $D1
issue root-b-ca root-b.key root-b.crt int-x.csr int-x-by-b.crt v3_inter $D0 $D1

# expired intermediate: validity window entirely in the past
rsa_key int-exp.key; csr int-exp.key int-exp.csr "Intermediate Expired"
issue root-a-ca root-a.key root-a.crt int-exp.csr int-exp.crt v3_inter \
  20190101000000Z 20200101000000Z

# name-constrained subtree
new_ca_dir int-nc-ca
rsa_key int-nc.key; csr int-nc.key int-nc.csr "Intermediate NC"
issue root-nc-ca root-nc.key root-nc.crt int-nc.csr int-nc.crt v3_inter $D0 $D1

# pathlen:0 intermediate with a sub-CA underneath
rsa_key int-p0.key; csr int-p0.key int-p0.csr "Intermediate PathLen0"
issue root-a-ca root-a.key root-a.crt int-p0.csr int-p0.crt v3_inter_p0 $D0 $D1
new_ca_dir int-p0-ca
rsa_key sub-p0.key; csr sub-p0.key sub-p0.csr "Sub CA Under PathLen0"
issue int-p0-ca int-p0.key int-p0.crt sub-p0.csr sub-p0.crt v3_inter $D0 $D1
new_ca_dir sub-p0-ca

# ---------------------------------------------------------- leaf key/csr --
for l in leaf-good leaf-cross leaf-exp leaf-fixed leaf-other \
         leaf-nc-good leaf-nc-outside leaf-nc-excluded leaf-p0; do
  rsa_key "$l.key"
done
openssl ecparam -name prime256v1 -genkey -noout -out leaf-ec.key
rsa_key leaf-self.key

csr leaf-good.key      leaf-good.csr      "Good Leaf"
csr leaf-cross.key     leaf-cross.csr     "Cross Leaf"
csr leaf-exp.key       leaf-exp.csr       "Leaf Under Expired CA"
csr leaf-fixed.key     leaf-fixed.csr     "Fixed Window Leaf"
csr leaf-other.key     leaf-other.csr     "Leaf Under Root B"
csr leaf-nc-good.key   leaf-nc-good.csr   "NC Good Leaf"
csr leaf-nc-outside.key leaf-nc-outside.csr "NC Outside Leaf"
csr leaf-nc-excluded.key leaf-nc-excluded.csr "NC Excluded Leaf"
csr leaf-p0.key        leaf-p0.csr        "Leaf Under PathLen"
csr leaf-ec.key        leaf-ec.csr        "EC Leaf"
csr leaf-self.key      leaf-self.csr      "Self Signed Leaf"

# ---------------------------------------------------------------- leaves --
issue int-ca1-ca int-ca1.key int-ca1.crt leaf-good.csr leaf-good.crt v3_leaf $D0 $D1
new_ca_dir int-x-ca
issue int-x-ca int-x.key int-x-by-a.crt leaf-cross.csr leaf-cross.crt v3_leaf $D0 $D1
new_ca_dir int-exp-ca
issue int-exp-ca int-exp.key int-exp.crt leaf-exp.csr leaf-exp.crt v3_leaf \
  20190601000000Z 20191231000000Z
issue int-ca1-ca int-ca1.key int-ca1.crt leaf-fixed.csr leaf-fixed.crt v3_leaf \
  20260101000000Z 20270101000000Z
issue root-b-ca root-b.key root-b.crt leaf-other.csr leaf-other.crt v3_leaf $D0 $D1
issue int-nc-ca int-nc.key int-nc.crt leaf-nc-good.csr leaf-nc-good.crt v3_leaf_good $D0 $D1
issue int-nc-ca int-nc.key int-nc.crt leaf-nc-outside.csr leaf-nc-outside.crt v3_leaf_outside $D0 $D1
issue int-nc-ca int-nc.key int-nc.crt leaf-nc-excluded.csr leaf-nc-excluded.crt v3_leaf_excluded $D0 $D1
issue sub-p0-ca sub-p0.key sub-p0.crt leaf-p0.csr leaf-p0.crt v3_leaf $D0 $D1
issue int-ca1-ca int-ca1.key int-ca1.crt leaf-ec.csr leaf-ec.crt v3_leaf $D0 $D1
new_ca_dir leaf-self-ca
selfsign leaf-self-ca leaf-self.key leaf-self.csr leaf-self.crt v3_leaf $D0 $D1

find . -name '*.csr' -delete
echo "fixtures generated:"; ls *.crt
