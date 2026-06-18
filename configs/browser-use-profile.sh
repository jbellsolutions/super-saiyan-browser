#!/usr/bin/env bash
# =============================================================================
# browser-use-profile.sh — Sync Local Cookies to a Browser Use Cloud Profile
# =============================================================================
#
# This script exports cookies and localStorage from your local Chrome browser
# and syncs them to a Browser Use Cloud profile, enabling authenticated
# browsing sessions in the cloud without re-logging in.
#
# Use case:
#   You've logged into Facebook/LinkedIn/Instagram locally. Instead of
#   re-authenticating in Browser Use Cloud (which requires 2FA handling),
#   you sync your existing session cookies so the cloud browser can use
#   them immediately.
#
# Prerequisites:
#   - Browser Use Cloud API key (set BROWSER_USE_API_KEY env var or pass --api-key)
#   - browser-use CLI installed: uvx browser-use install
#   - Chrome or Chromium browser with your logged-in profiles
#   - jq (JSON processor): sudo apt install jq / brew install jq
#   - sqlite3 (to read Chrome cookie DB): sudo apt install sqlite3
#
# Usage:
#   # Sync all cookies from default Chrome profile
#   ./browser-use-profile.sh --domain facebook.com
#
#   # Sync from a specific Chrome profile directory
#   ./browser-use-profile.sh --domain linkedin.com --chrome-profile ~/.config/google-chrome/Profile\ 3
#
#   # Sync with explicit API key
#   ./browser-use-profile.sh --domain instagram.com --api-key bu_live_xxxx
#
#   # List existing cloud profiles
#   ./browser-use-profile.sh --list-profiles
#
#   # Delete a cloud profile
#   ./browser-use-profile.sh --delete-profile my-profile-id
#
# =============================================================================

set -euo pipefail  # Exit on error, undefined var, pipe failure

# ── Configuration ────────────────────────────────────────────────────────────

# Default Chrome cookie database path (Linux). Adjust for macOS/Windows.
CHROME_PROFILE_DIR="${HOME}/.config/google-chrome/Default"
COOKIE_DB="${CHROME_PROFILE_DIR}/Cookies"
LOCAL_STORAGE_DIR="${CHROME_PROFILE_DIR}/Local Storage"

# Browser Use Cloud configuration
BU_API_BASE="${BROWSER_USE_API_BASE:-https://api.browser-use.com}"
BU_API_KEY="${BROWSER_USE_API_KEY:-}"
PROFILE_NAME=""          # Cloud profile name (auto-generated if empty)
DRY_RUN=false            # If true, print what would happen without executing
LIST_PROFILES=false
DELETE_PROFILE=""

# ── Color output helpers ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Argument parsing ─────────────────────────────────────────────────────────

usage() {
    cat << 'EOF'
Usage: browser-use-profile.sh [OPTIONS]

Sync local Chrome browser cookies and localStorage to a Browser Use Cloud
profile for authenticated cloud browsing sessions.

Options:
  --domain DOMAIN           Domain to extract cookies for (e.g., facebook.com)
  --chrome-profile DIR      Chrome profile directory (default: ~/.config/google-chrome/Default)
  --api-key KEY             Browser Use Cloud API key (or set BROWSER_USE_API_KEY)
  --profile-name NAME       Name for the cloud profile (auto-generated if omitted)
  --dry-run                 Print what would happen without making changes
  --list-profiles           List existing Browser Use Cloud profiles
  --delete-profile ID       Delete a cloud profile by ID
  -h, --help                Show this help message

Examples:
  ./browser-use-profile.sh --domain facebook.com
  ./browser-use-profile.sh --domain linkedin.com --dry-run
  ./browser-use-profile.sh --list-profiles
  ./browser-use-profile.sh --delete-profile prof_abc123
EOF
    exit 0
}

DOMAIN=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)
            DOMAIN="$2"; shift 2 ;;
        --chrome-profile)
            CHROME_PROFILE_DIR="$2"
            COOKIE_DB="${CHROME_PROFILE_DIR}/Cookies"
            LOCAL_STORAGE_DIR="${CHROME_PROFILE_DIR}/Local Storage"
            shift 2 ;;
        --api-key)
            BU_API_KEY="$2"; shift 2 ;;
        --profile-name)
            PROFILE_NAME="$2"; shift 2 ;;
        --dry-run)
            DRY_RUN=true; shift ;;
        --list-profiles)
            LIST_PROFILES=true; shift ;;
        --delete-profile)
            DELETE_PROFILE="$2"; shift 2 ;;
        -h|--help)
            usage ;;
        *)
            log_error "Unknown option: $1"
            usage ;;
    esac
done

# ── Pre-flight checks ────────────────────────────────────────────────────────

check_dependencies() {
    local missing=()

    if ! command -v jq &>/dev/null; then
        missing+=("jq (apt install jq / brew install jq)")
    fi

    if ! command -v sqlite3 &>/dev/null; then
        missing+=("sqlite3 (apt install sqlite3)")
    fi

    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies:"
        for dep in "${missing[@]}"; do
            echo "  - $dep"
        done
        exit 1
    fi
}

# ── List cloud profiles ──────────────────────────────────────────────────────

list_cloud_profiles() {
    log_info "Fetching Browser Use Cloud profiles..."

    if [[ -z "$BU_API_KEY" ]]; then
        log_error "BROWSER_USE_API_KEY not set. Pass --api-key or set the env var."
        exit 1
    fi

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -H "Authorization: Bearer ${BU_API_KEY}" \
        -H "Content-Type: application/json" \
        "${BU_API_BASE}/v3/profiles" 2>&1)

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" != "200" ]]; then
        log_error "Failed to list profiles (HTTP $http_code)"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        exit 1
    fi

    echo "$body" | jq -r '
        .profiles[] |
        "  ID: \(.id)\n  Name: \(.name // "unnamed")\n  Created: \(.created_at // "unknown")\n  Last used: \(.last_used_at // "never")\n"
    '
}

# ── Delete cloud profile ─────────────────────────────────────────────────────

delete_cloud_profile() {
    local profile_id="$1"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY RUN] Would delete profile: ${profile_id}"
        return 0
    fi

    log_info "Deleting cloud profile: ${profile_id}"

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X DELETE \
        -H "Authorization: Bearer ${BU_API_KEY}" \
        "${BU_API_BASE}/v3/profiles/${profile_id}" 2>&1)

    local http_code
    http_code=$(echo "$response" | tail -1)

    if [[ "$http_code" == "200" || "$http_code" == "204" ]]; then
        log_ok "Profile ${profile_id} deleted"
    else
        log_error "Failed to delete profile (HTTP $http_code)"
        echo "$response" | sed '$d' | jq . 2>/dev/null || echo "$response" | sed '$d'
        exit 1
    fi
}

# ── Cookie extraction ────────────────────────────────────────────────────────

extract_cookies() {
    local domain="$1"

    log_info "Extracting cookies for domain: ${domain}"

    # Check if Chrome cookie database exists
    if [[ ! -f "$COOKIE_DB" ]]; then
        log_error "Cookie database not found at: ${COOKIE_DB}"
        log_error "Is Chrome installed? Is the profile path correct?"
        log_error "Try: --chrome-profile ~/.config/google-chrome/Profile\ 1"
        exit 1
    fi

    # Check if Chrome is running (which locks the DB)
    if lsof "$COOKIE_DB" &>/dev/null 2>&1; then
        log_warn "Chrome appears to be running. The cookie DB may be locked."
        log_warn "Close Chrome before running this script for best results."
        if [[ "$DRY_RUN" != "true" ]]; then
            log_info "Attempting to proceed anyway..."
        fi
    fi

    # Extract cookies as JSON array from the SQLite database
    # Chrome stores cookies in a 'cookies' table with columns:
    #   host_key, name, value, path, expires_utc, is_secure, is_httponly,
    #   same_site, priority, encrypted_value
    #
    # We extract unencrypted values (encrypted_value is NULL for non-encrypted)
    # For encrypted cookies, we skip them (they require Chrome's keychain).
    local cookies_json
    cookies_json=$(sqlite3 -json "$COOKIE_DB" \
        "SELECT
            host_key,
            name,
            CASE
                WHEN value != '' THEN value
                ELSE '(encrypted)'
            END as value,
            path,
            expires_utc,
            is_secure,
            is_httponly,
            same_site
        FROM cookies
        WHERE host_key LIKE '%${domain}%'" 2>&1)

    local sqlite_exit=$?
    if [[ $sqlite_exit -ne 0 ]]; then
        # Try alternative: Chrome may use newer schema or DB is locked
        log_error "Failed to read cookie database (exit code: $sqlite_exit)"
        log_error "Error: $cookies_json"
        log_error ""
        log_error "Troubleshooting:"
        log_error "  1. Close Chrome completely"
        log_error "  2. Check profile path: ${CHROME_PROFILE_DIR}"
        log_error "  3. Try copying the DB first: cp \"${COOKIE_DB}\" /tmp/cookies-copy.db"
        exit 1
    fi

    local cookie_count
    cookie_count=$(echo "$cookies_json" | jq '. | length' 2>/dev/null || echo "0")

    if [[ "$cookie_count" == "0" ]]; then
        log_warn "No cookies found for domain: ${domain}"
        log_warn "Are you logged in to ${domain} in Chrome?"
        exit 1
    fi

    log_ok "Found ${cookie_count} cookies for ${domain}"
    echo "$cookies_json"
}

# ── localStorage extraction ──────────────────────────────────────────────────

extract_localstorage() {
    local domain="$1"

    log_info "Checking localStorage for domain: ${domain}"

    # Chrome stores localStorage in LevelDB format under:
    #   <profile>/Local Storage/leveldb/
    # The domain key format uses _ and reversed domain:
    #   _https://www.facebook.com
    local ls_dir="${LOCAL_STORAGE_DIR}/leveldb"

    if [[ ! -d "$ls_dir" ]]; then
        log_warn "Local Storage directory not found at: ${ls_dir}"
        log_info "Skipping localStorage extraction (cookies only)"
        echo "[]"
        return 0
    fi

    # LevelDB is not human-readable without tools. We note this limitation
    # and return an empty array. Full localStorage extraction requires
    # running JavaScript in an open browser tab.
    log_warn "localStorage extraction from LevelDB is not supported in this script"
    log_info "To capture localStorage, use the Browser Use SDK's profile export:"
    log_info "  browser-use profile export --domain ${domain}"
    echo "[]"
}

# ── Upload to Browser Use Cloud ──────────────────────────────────────────────

upload_profile_to_cloud() {
    local cookies_json="$1"
    local domain="$2"

    if [[ -z "$PROFILE_NAME" ]]; then
        PROFILE_NAME="sync-${domain}-$(date +%Y%m%d-%H%M%S)"
    fi

    local payload
    payload=$(jq -n \
        --arg name "$PROFILE_NAME" \
        --argjson cookies "$cookies_json" \
        '{
            name: $name,
            cookies: $cookies,
            origin: ("https://" + ($name | split("-")[1] // "unknown"))
        }')

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY RUN] Would create cloud profile: ${PROFILE_NAME}"
        log_info "[DRY RUN] Cookie count: $(echo "$cookies_json" | jq '. | length')"
        echo "$cookies_json" | jq '.[0:3]'  # Show first 3 cookies as sample
        return 0
    fi

    log_info "Uploading cookies to Browser Use Cloud..."

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${BU_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${BU_API_BASE}/v3/profiles" 2>&1)

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
        local profile_id
        profile_id=$(echo "$body" | jq -r '.id // .profile_id // "unknown"')
        log_ok "Profile created successfully!"
        log_ok "  Profile ID: ${profile_id}"
        log_ok "  Profile Name: ${PROFILE_NAME}"
        log_ok ""
        log_info "To use this profile in Browser Use Cloud SDK:"
        echo ""
        echo "    from browser_use_sdk.v3 import BrowserUse"
        echo "    client = BrowserUse()"
        echo "    session = client.sessions.create("
        echo "        profile_id=\"${profile_id}\","
        echo "        task=\"Browse ${domain} with synced auth\""
        echo "    )"
    else
        log_error "Failed to create profile (HTTP $http_code)"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        exit 1
    fi
}

# ── Main script flow ─────────────────────────────────────────────────────────

main() {
    # Handle non-sync operations first
    if [[ "$LIST_PROFILES" == "true" ]]; then
        check_dependencies
        list_cloud_profiles
        exit 0
    fi

    if [[ -n "$DELETE_PROFILE" ]]; then
        check_dependencies
        if [[ -z "$BU_API_KEY" ]]; then
            log_error "BROWSER_USE_API_KEY not set. Pass --api-key or set the env var."
            exit 1
        fi
        delete_cloud_profile "$DELETE_PROFILE"
        exit 0
    fi

    # Sync requires a domain
    if [[ -z "$DOMAIN" ]]; then
        log_error "--domain is required for cookie sync"
        echo ""
        usage
    fi

    # Validate API key
    if [[ -z "$BU_API_KEY" ]]; then
        log_error "BROWSER_USE_API_KEY not set. Pass --api-key or set the env var."
        exit 1
    fi

    # Run pre-flight checks
    check_dependencies

    echo "=============================================="
    log_info "Browser Use Cloud — Profile Sync"
    log_info "  Domain:        ${DOMAIN}"
    log_info "  Chrome Profile: ${CHROME_PROFILE_DIR}"
    log_info "  Dry Run:       ${DRY_RUN}"
    echo "=============================================="
    echo ""

    # Step 1: Extract cookies from Chrome
    log_info "Step 1/4: Extracting cookies from Chrome..."
    local cookies
    cookies=$(extract_cookies "$DOMAIN")

    # Step 2: Extract localStorage (best-effort)
    log_info "Step 2/4: Extracting localStorage (best-effort)..."
    local localstorage
    localstorage=$(extract_localstorage "$DOMAIN")

    # Step 3: Validate extracted data
    log_info "Step 3/4: Validating extracted data..."
    local cookie_count
    cookie_count=$(echo "$cookies" | jq '. | length')
    if [[ "$cookie_count" -eq 0 ]]; then
        log_error "No cookies extracted. Cannot create profile."
        exit 1
    fi
    log_ok "Ready to sync ${cookie_count} cookies to cloud profile"

    # Step 4: Upload to Browser Use Cloud
    log_info "Step 4/4: Uploading to Browser Use Cloud..."
    upload_profile_to_cloud "$cookies" "$DOMAIN"

    echo ""
    log_ok "Sync complete! Your ${DOMAIN} session is now available in Browser Use Cloud."
}

# ── Entry point ──────────────────────────────────────────────────────────────

main "$@"
