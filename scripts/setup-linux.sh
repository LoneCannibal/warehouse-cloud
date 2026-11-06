#!/usr/bin/env bash
# Installs everything needed to test, run and deploy this project, on Debian 13 or
# Ubuntu 24.04 and later. On Windows, scripts/setup-windows.ps1 sets up Ubuntu in WSL
# and runs this script there.
#
#   ./scripts/setup-linux.sh               everything
#   ./scripts/setup-linux.sh --no-docker   skip Docker, which only the local run and the
#                                          integration tests need
#   ./scripts/setup-linux.sh --no-sudo     only what installs into your home folder
#
# It is safe to run again: each step looks at what is already there first.
#
# - apt, with sudo: git, make, Python, jq, the GitHub CLI, and Docker from Docker's
#   own repository.
# - Your home folder, with no sudo: Terraform, kustomize, Helm, kubeconform, k6,
#   Maven, a Java 11 JDK and the Google Cloud CLI. Each is pinned to the version
#   below and checked against the SHA-256 recorded here, so a broken download is
#   refused, not installed. A copy that is missing, broken or a different
#   version is replaced.
# - A Python virtual environment in .venv, with the project's packages.
set -euo pipefail

# Pinned versions. Change one only together with its checksums below.
TERRAFORM=1.17.1
KUSTOMIZE=5.8.3
HELM=4.3.1
KUBECONFORM=0.8.0
K6=2.3.0 # the version the load test ran with on Google Cloud
MAVEN=3.9.16
JDK_RELEASE=11.0.32.1+1 # Temurin. Dataproc runs Flink 1.17 on Java 11.
GCLOUD=592.0.0          # gcloud updates itself afterwards: gcloud components update

declare -A SHA256=(
  [terraform-amd64]=4638628058e71a55212b1c563712eac03b902128f40740f3dc8174df12135061
  [terraform-arm64]=988e950a1c23058093b0e66ab2d5fb53fc18345ecfbd5871acf51dbac9325edd
  [kustomize-amd64]=cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61
  [kustomize-arm64]=9867b76482cfc6d25b546abc1d2b5a32bd137505aba640649b4b378f64f6d68f
  [helm-amd64]=0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b
  [helm-arm64]=8cf8f66ecbc252ac8f7f06cca68aba88508ddfde3a6a34feb1678ad1694b4f88
  [kubeconform-amd64]=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
  [kubeconform-arm64]=1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87
  [k6-amd64]=39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1
  [k6-arm64]=5ca3433e8201da72a284aaa241a1bb5fb47f4abb4e384d39410ddd8062f49b90
  [jdk-amd64]=5c3f68887c325d36d852ba534303e1f5f1f5cae7d6cc1e951d73e0d8e98a058d
  [jdk-arm64]=f27033e6f7523c1b0b2565a78e9c0e0abe5596a854ce00ca04ec1b06ece7a935
  [gcloud-amd64]=2d841a23a681ab94486af6841e5f1cd123cd300fb8c4be6ba5b51350fca3d50b
  [gcloud-arm64]=ba588759c4c42122a8c14536009738c0f0030989ca75788235f9956555e44f4d
)
MAVEN_SHA512=831a8591fe20c8243b1dbe7d71e3244f31d1665b0804b2e825e38cbbe5ce0cafb8338851f90780735568773e0a6cd07bbec107cda0b896b008b861075358b6f6

WITH_DOCKER=1
WITH_SUDO=1
for arg in "$@"; do
  case "$arg" in
    --no-docker) WITH_DOCKER=0 ;;
    --no-sudo) WITH_SUDO=0 WITH_DOCKER=0 ;;
    -h | --help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() {
  printf '\nerror: %s\n' "$*" >&2
  exit 1
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[ -f "$ROOT/Makefile" ] && [ -d "$ROOT/infra" ] || die "run this from a copy of the repository"
cd "$ROOT"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ARCH=amd64 JDK_ARCH=x64 GCLOUD_ARCH=x86_64 ;;
  Linux-aarch64 | Linux-arm64) ARCH=arm64 JDK_ARCH=aarch64 GCLOUD_ARCH=arm ;;
  *) die "this script supports Linux on x86-64 or ARM64, not $(uname -sm). On Windows, use scripts/setup-windows.ps1." ;;
esac

BIN="$HOME/.local/bin"
OPT="$HOME/.local/opt"
mkdir -p "$BIN" "$OPT"
export PATH="$BIN:$HOME/google-cloud-sdk/bin:$PATH"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

# True when `name args...` runs and its output contains `want`. The output is
# captured first: piping into `grep -q` would let the tool die of SIGPIPE, which
# pipefail counts as a failure.
reports() {
  local name=$1 want=$2 out
  shift 2
  command -v "$name" > /dev/null 2>&1 || return 1
  out=$("$name" "$@" 2>&1) || return 1
  [[ $out == *"$want"* ]]
}

# Downloads a file into $TMP and checks it against the expected SHA-256 or SHA-512,
# trying each URL in turn. Prints the path of the verified file.
fetch() { # want url [fallback-url...]
  local want=$1 url out got
  shift
  out="$TMP/${1##*/}"
  printf '   downloading %s\n' "${1##*/}" >&2
  for url in "$@"; do
    curl -fL --retry 3 --retry-all-errors --retry-delay 2 --silent --show-error -o "$out" "$url" || continue
    if [ ${#want} -eq 128 ]; then
      got=$(sha512sum "$out" | cut -d' ' -f1)
    else
      got=$(sha256sum "$out" | cut -d' ' -f1)
    fi
    if [ "$got" = "$want" ]; then
      printf '%s' "$out"
      return 0
    fi
    printf '   checksum mismatch for %s: the download is incomplete or not the expected file\n' "$url" >&2
  done
  die "could not download ${1##*/}. Nothing was installed for it; run the script again."
}

# Extracts one file from a .tar.gz and installs it into ~/.local/bin.
install_from_tar() { # archive member name
  mkdir -p "$TMP/x"
  tar -xzf "$1" -C "$TMP/x" "$2"
  install -m 0755 "$TMP/x/$2" "$BIN/$3"
  rm -rf "$TMP/x"
}

# ---------------------------------------------------------------- system packages

APT_PACKAGES=(ca-certificates curl git make python3 python3-venv python3-pip jq gh openssh-client)

python_ok() {
  python3 -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 12))' 2> /dev/null
}

if [ "$WITH_SUDO" -eq 1 ]; then
  say "System packages"
  command -v apt-get > /dev/null || die "apt-get not found. This script supports Debian and Ubuntu; elsewhere, install ${APT_PACKAGES[*]} yourself and run it with --no-sudo."
  missing=()
  for pkg in "${APT_PACKAGES[@]}"; do
    dpkg -s "$pkg" > /dev/null 2>&1 || missing+=("$pkg")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    note "installing with apt: ${missing[*]}"
    [ ${#SUDO[@]} -eq 0 ] || command -v sudo > /dev/null || die "sudo not found. Run as root, or with --no-sudo."
    "${SUDO[@]}" apt-get update -q
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${missing[@]}"
  else
    note "already installed"
  fi
else
  say "Checking the commands this script needs"
  for cmd in curl git make python3 tar sha256sum; do
    command -v "$cmd" > /dev/null || die "$cmd is missing. Install it, or run without --no-sudo."
  done
  note "ok"
fi
python_ok || die "Python 3.12 or later, with venv, is needed. This system has $(python3 --version 2>&1). Debian 13 and Ubuntu 24.04 have it."

# ------------------------------------------------------------------------- Docker

if [ "$WITH_DOCKER" -eq 1 ]; then
  say "Docker"
  if reports docker "" compose version; then
    note "already installed: $(docker compose version)"
  elif command -v docker > /dev/null; then
    note "Docker is installed without the Compose plugin; install docker-compose-plugin to use make local-up"
  else
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
      ubuntu:* | *:*ubuntu*) DOCKER_DISTRO=ubuntu CODENAME=${UBUNTU_CODENAME:-$VERSION_CODENAME} ;;
      debian:*) DOCKER_DISTRO=debian CODENAME=$VERSION_CODENAME ;;
      *) die "Docker's repository has no packages for ${ID:-this system}. Install Docker yourself, or run with --no-docker." ;;
    esac
    note "adding Docker's apt repository for $DOCKER_DISTRO $CODENAME"
    "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$DOCKER_DISTRO/gpg" -o "$TMP/docker.asc"
    "${SUDO[@]}" install -m 0644 "$TMP/docker.asc" /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$DOCKER_DISTRO $CODENAME stable" |
      "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list > /dev/null
    "${SUDO[@]}" apt-get update -q
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    if [ "$(id -u)" -ne 0 ]; then
      "${SUDO[@]}" usermod -aG docker "$(id -un)"
      DOCKER_GROUP_ADDED=1
    fi
    if [ -d /run/systemd/system ]; then
      "${SUDO[@]}" systemctl enable --now docker
    else
      note "systemd is not running here, so Docker will not start by itself. Start it with: sudo service docker start"
    fi
  fi
fi

# ---------------------------------------------------- tools in your home folder

say "Terraform $TERRAFORM"
if reports terraform "Terraform v$TERRAFORM" version; then
  note "already installed"
else
  f=$(fetch "${SHA256[terraform-$ARCH]}" "https://releases.hashicorp.com/terraform/$TERRAFORM/terraform_${TERRAFORM}_linux_$ARCH.zip")
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("terraform", sys.argv[2])' "$f" "$TMP"
  install -m 0755 "$TMP/terraform" "$BIN/terraform"
  note "installed in $BIN"
fi

say "kustomize $KUSTOMIZE"
if reports kustomize "v$KUSTOMIZE" version; then
  note "already installed"
else
  f=$(fetch "${SHA256[kustomize-$ARCH]}" "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv$KUSTOMIZE/kustomize_v${KUSTOMIZE}_linux_$ARCH.tar.gz")
  install_from_tar "$f" kustomize kustomize
  note "installed in $BIN"
fi

say "Helm $HELM"
if reports helm "v$HELM" version --short; then
  note "already installed"
else
  f=$(fetch "${SHA256[helm-$ARCH]}" "https://get.helm.sh/helm-v$HELM-linux-$ARCH.tar.gz")
  install_from_tar "$f" "linux-$ARCH/helm" helm
  note "installed in $BIN"
fi

say "kubeconform $KUBECONFORM"
if reports kubeconform "v$KUBECONFORM" -v; then
  note "already installed"
else
  f=$(fetch "${SHA256[kubeconform-$ARCH]}" "https://github.com/yannh/kubeconform/releases/download/v$KUBECONFORM/kubeconform-linux-$ARCH.tar.gz")
  install_from_tar "$f" kubeconform kubeconform
  note "installed in $BIN"
fi

say "k6 $K6"
if reports k6 "k6 v$K6" version; then
  note "already installed"
else
  f=$(fetch "${SHA256[k6-$ARCH]}" "https://github.com/grafana/k6/releases/download/v$K6/k6-v$K6-linux-$ARCH.tar.gz")
  install_from_tar "$f" "k6-v$K6-linux-$ARCH/k6" k6
  note "installed in $BIN"
fi

say "Java $JDK_RELEASE, for Maven only"
JDK_HOME="$OPT/jdk-$JDK_RELEASE"
if reports "$JDK_HOME/bin/java" "${JDK_RELEASE%+*}" -version; then
  note "already installed"
else
  jdk_file="OpenJDK11U-jdk_${JDK_ARCH}_linux_hotspot_${JDK_RELEASE/+/_}.tar.gz"
  f=$(fetch "${SHA256[jdk-$ARCH]}" "https://github.com/adoptium/temurin11-binaries/releases/download/jdk-${JDK_RELEASE/+/%2B}/$jdk_file")
  rm -rf "$JDK_HOME"
  tar -xzf "$f" -C "$OPT"
  [ -x "$JDK_HOME/bin/java" ] || die "the JDK archive did not unpack to $JDK_HOME"
  note "installed in $JDK_HOME. Your default java is unchanged."
fi

say "Maven $MAVEN"
MAVEN_HOME="$OPT/apache-maven-$MAVEN"
if reports mvn "Apache Maven $MAVEN" -v && reports mvn "Java version: ${JDK_RELEASE%+*}" -v; then
  note "already installed"
else
  if ! reports "$MAVEN_HOME/bin/mvn" "Apache Maven $MAVEN" -v; then
    f=$(fetch "$MAVEN_SHA512" \
      "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/$MAVEN/apache-maven-$MAVEN-bin.tar.gz" \
      "https://archive.apache.org/dist/maven/maven-3/$MAVEN/binaries/apache-maven-$MAVEN-bin.tar.gz")
    rm -rf "$MAVEN_HOME"
    tar -xzf "$f" -C "$OPT"
  fi
  # A small wrapper, so Maven always builds with Java 11, whatever your default is.
  rm -f "$BIN/mvn"
  cat > "$BIN/mvn" << EOF
#!/usr/bin/env bash
# Written by scripts/setup-linux.sh. Builds with Java 11, which Flink 1.17 on Dataproc runs.
export JAVA_HOME="$JDK_HOME"
exec "$MAVEN_HOME/bin/mvn" "\$@"
EOF
  chmod 0755 "$BIN/mvn"
  reports mvn "Apache Maven $MAVEN" -v || die "Maven does not run after installing it"
  note "installed in $MAVEN_HOME"
fi

say "Google Cloud CLI"
install_gcloud() {
  [ -e "$HOME/google-cloud-sdk" ] &&
    die "$HOME/google-cloud-sdk exists but its gcloud does not work. Move that folder away and run this script again."
  f=$(fetch "${SHA256[gcloud-$ARCH]}" "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-$GCLOUD-linux-$GCLOUD_ARCH.tar.gz")
  tar -xzf "$f" -C "$HOME"
  "$HOME/google-cloud-sdk/install.sh" --quiet --usage-reporting false --path-update true \
    --command-completion true --rc-path "$HOME/.bashrc" > "$TMP/gcloud-install.log" 2>&1 ||
    {
      cat "$TMP/gcloud-install.log" >&2
      die "the Google Cloud CLI installer failed"
    }
  hash -r
  note "installed in $HOME/google-cloud-sdk"
}
add_components() {
  gcloud components install kubectl gke-gcloud-auth-plugin --quiet > "$TMP/components.log" 2>&1
}
if reports gcloud "Google Cloud SDK" version; then
  note "already installed: $(command -v gcloud)"
else
  install_gcloud
fi
# kubectl and the GKE sign-in plugin, as gcloud components. A gcloud installed by
# apt or snap cannot add components, so a copy of our own goes in ~/google-cloud-sdk,
# which comes first on PATH.
if ! command -v kubectl > /dev/null || ! command -v gke-gcloud-auth-plugin > /dev/null; then
  note "adding kubectl and gke-gcloud-auth-plugin"
  if ! add_components; then
    if [ "$(command -v gcloud)" = "$HOME/google-cloud-sdk/bin/gcloud" ]; then
      cat "$TMP/components.log" >&2
      die "could not install kubectl and gke-gcloud-auth-plugin"
    fi
    note "$(command -v gcloud) cannot add components, so installing a separate copy"
    install_gcloud
    add_components || {
      cat "$TMP/components.log" >&2
      die "could not install kubectl and gke-gcloud-auth-plugin"
    }
  fi
  hash -r
fi

# ------------------------------------------------------------------ Python, PATH

say "Python environment in .venv"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
make -s install PYTHON=.venv/bin/python > "$TMP/pip.log" 2>&1 || {
  cat "$TMP/pip.log" >&2
  die "installing the Python packages failed"
}
note "ready: source .venv/bin/activate"

if ! grep -qs 'setup-linux.sh: ~/.local/bin' "$HOME/.bashrc"; then
  cat >> "$HOME/.bashrc" << 'EOF'

# Added by warehouse-cloud scripts/setup-linux.sh: ~/.local/bin first on PATH.
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
EOF
fi

# ------------------------------------------------------------------------ summary

say "What is on your PATH now"
problems=0
check() { # name expected-text args...
  local name=$1 want=$2 path line
  shift 2
  path=$(command -v "$name" || true)
  line=$("$name" "$@" 2>&1 | head -1 || true)
  if [ -n "$path" ] && reports "$name" "$want" "$@"; then
    printf '   ok    %-24s %s\n' "$name" "$line"
  else
    printf '   FAIL  %-24s %s\n' "$name" "${path:-not found}${line:+: $line}"
    problems=$((problems + 1))
  fi
}
check terraform "Terraform v$TERRAFORM" version
check kustomize "v$KUSTOMIZE" version
check helm "v$HELM" version --short
check kubeconform "v$KUBECONFORM" -v
check k6 "k6 v$K6" version
check mvn "Apache Maven $MAVEN" -v
check gcloud "Google Cloud SDK" version
check kubectl "Client Version" version --client
check gke-gcloud-auth-plugin "" --version
check git "git version" --version
check make "GNU Make" --version
if [ "$WITH_SUDO" -eq 1 ] || command -v gh > /dev/null; then
  check gh "gh version" --version
fi
check .venv/bin/python "Python 3" --version
if [ "$WITH_DOCKER" -eq 1 ]; then
  if [ "${DOCKER_GROUP_ADDED:-0}" -eq 1 ]; then
    note "docker: installed. It works without sudo once you log out and back in."
  else
    check docker "" compose version
  fi
fi

RELOGIN=""
if [ "${DOCKER_GROUP_ADDED:-0}" -eq 1 ]; then
  RELOGIN=$'\n     For Docker without sudo, log out and back in first (on WSL: wsl --shutdown).'
fi

if [ "$problems" -gt 0 ]; then
  echo
  echo "$problems tool(s) above are missing or a different version. If another copy comes"
  echo "earlier on your PATH, put $BIN first, or remove that copy."
  exit 1
fi

cat << EOF

Everything is installed. Next:
  1. Open a new terminal, so the PATH changes apply.$RELOGIN
  2. cd "$ROOT" && source .venv/bin/activate
  3. Sign in to Google Cloud, if you will deploy:
       gcloud auth login
       gcloud auth application-default login
     If no browser opens, copy the link gcloud prints into one.
  4. Sign in to GitHub, to push:  gh auth login && gh auth setup-git
  5. Run the tests:
       docker compose up -d zookeeper-1 zookeeper-2 zookeeper-3 postgres
       make test
     Then follow "Deploy to Google Cloud" in README.md, from step 4.
EOF
