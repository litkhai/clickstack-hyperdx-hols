#!/bin/bash
# ============================================================================
# CH2OTEL: ClickHouse System Metrics to OpenTelemetry Converter - Setup Script
# ============================================================================
# Description: Interactive setup script for CH2OTEL (CHC Self-Service Only)
# Version: 1.0.0
# Date: 2025-12-08
# ============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Secret files
CREDENTIALS_FILE="${SCRIPT_DIR}/.credentials"
CONFIG_FILE="${SCRIPT_DIR}/ch2otel.conf"

# Detect which clickhouse client command is available
CLICKHOUSE_CLIENT_CMD=""
if command -v clickhouse-client &> /dev/null || which clickhouse-client &> /dev/null; then
    CLICKHOUSE_CLIENT_CMD="clickhouse-client"
elif command -v clickhouse &> /dev/null || which clickhouse &> /dev/null; then
    CLICKHOUSE_CLIENT_CMD="clickhouse client"
fi

# Function to print colored messages
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${CYAN}$1${NC}"
}

# Function to prompt for input with default value
prompt_input() {
    local prompt_text=$1
    local default_value=$2
    local var_name=$3

    if [ -n "$default_value" ]; then
        read -p "$(echo -e ${BLUE}${prompt_text}${NC} [${default_value}]: )" input_value
        input_value=${input_value:-$default_value}
    else
        read -p "$(echo -e ${BLUE}${prompt_text}${NC}: )" input_value
    fi

    eval "$var_name='$input_value'"
}

# Function to prompt for password (hidden input)
prompt_password() {
    local prompt_text=$1
    local var_name=$2

    read -s -p "$(echo -e ${BLUE}${prompt_text}${NC}: )" input_value
    echo
    eval "$var_name='$input_value'"
}

# Banner
echo -e "${GREEN}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                      CH2OTEL                                 ║
║  ClickHouse System Metrics to OpenTelemetry Converter        ║
║                    Version 1.0.0                             ║
║            (CHC Self-Service Monitoring)                     ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

print_info "CH2OTEL은 ClickHouse Cloud의 시스템 메트릭을 OTEL 형식으로 변환합니다."
print_info "RMV와 시스템 테이블을 활용한 자기 서비스 전용 모니터링 솔루션입니다."
print_warning "⚠️  제한사항: 현재 서비스만 모니터링 가능 (org 내 다른 서비스 미지원)"
echo ""

# ============================================================================
# Step 1: Check for existing credentials
# ============================================================================
if [ -f "$CREDENTIALS_FILE" ]; then
    print_warning "기존 인증 정보가 발견되었습니다."
    prompt_input "기존 인증 정보를 사용하시겠습니까? (yes/no)" "yes" USE_EXISTING_CREDS

    if [ "$USE_EXISTING_CREDS" = "yes" ] || [ "$USE_EXISTING_CREDS" = "y" ]; then
        source "$CREDENTIALS_FILE"
        print_success "기존 인증 정보를 불러왔습니다."
        SKIP_CREDENTIALS=true

        echo ""

        # Test connection with existing credentials
        print_info "기존 인증 정보로 CHC 연결을 테스트합니다..."
        CH_HOST_CLEAN=$(echo "$CH_HOST" | sed -E 's|^https?://||')

        VERSION=$(curl -s "https://${CH_HOST_CLEAN}:${CH_PORT}/?query=SELECT%20version()" --user "${CH_USER}:${CH_PASSWORD}" 2>&1)
        CURL_EXIT_CODE=$?

        if [ $CURL_EXIT_CODE -eq 0 ] && [ -n "$VERSION" ] && [[ ! "$VERSION" =~ "Code:" ]]; then
            print_success "CHC 연결 성공! (ClickHouse version: ${VERSION})"
        else
            print_error "CHC 연결 실패. 인증 정보가 만료되었거나 올바르지 않습니다."
            if [[ "$VERSION" =~ "Code:" ]]; then
                print_error "오류: ${VERSION}"
            fi
            print_error "다시 시작하려면 스크립트를 재실행하세요."
            exit 1
        fi

        echo ""
    else
        SKIP_CREDENTIALS=false
    fi
    echo ""
else
    SKIP_CREDENTIALS=false
fi

# ============================================================================
# Step 2: ClickHouse Cloud Connection Configuration
# ============================================================================
if [ "$SKIP_CREDENTIALS" = false ]; then
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_header "Step 1: ClickHouse Cloud Connection"
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    print_info "CHC 연결 정보를 입력하세요."
    echo ""

    # Validate CHC Host
    while true; do
        prompt_input "CHC 호스트 (예: abc123.us-east-1.aws.clickhouse.cloud)" "" CH_HOST

        # Remove https:// or http:// prefix if present
        CH_HOST=$(echo "$CH_HOST" | sed -E 's|^https?://||')

        # Validate host format
        if [ -z "$CH_HOST" ]; then
            print_error "호스트를 입력해주세요."
            continue
        fi

        if [[ ! "$CH_HOST" =~ \.clickhouse\.cloud$ ]]; then
            print_warning "경고: CHC 호스트는 일반적으로 '.clickhouse.cloud'로 끝납니다."
            prompt_input "계속하시겠습니까? (yes/no)" "no" CONFIRM_HOST
            if [ "$CONFIRM_HOST" != "yes" ] && [ "$CONFIRM_HOST" != "y" ]; then
                continue
            fi
        fi

        print_success "호스트: ${CH_HOST}"
        break
    done

    # Validate CHC Password
    while true; do
        prompt_password "CHC 비밀번호" CH_PASSWORD

        if [ -z "$CH_PASSWORD" ]; then
            print_error "비밀번호를 입력해주세요."
            continue
        fi

        if [ ${#CH_PASSWORD} -lt 8 ]; then
            print_warning "비밀번호가 너무 짧습니다 (최소 8자 권장)."
            prompt_input "계속하시겠습니까? (yes/no)" "no" CONFIRM_PWD
            if [ "$CONFIRM_PWD" != "yes" ] && [ "$CONFIRM_PWD" != "y" ]; then
                continue
            fi
        fi

        print_success "비밀번호가 입력되었습니다."
        break
    done

    # CHC 고정값
    CH_PORT=8443
    CH_USER=default

    echo ""

    # Test connection immediately after credential input
    print_info "입력하신 정보로 CHC 연결을 테스트합니다..."

    VERSION=$(curl -s "https://${CH_HOST}:${CH_PORT}/?query=SELECT%20version()" --user "${CH_USER}:${CH_PASSWORD}" 2>&1)
    CURL_EXIT_CODE=$?

    if [ $CURL_EXIT_CODE -eq 0 ] && [ -n "$VERSION" ] && [[ ! "$VERSION" =~ "Code:" ]]; then
        print_success "CHC 연결 성공! (ClickHouse version: ${VERSION})"
    else
        print_error "CHC 연결 실패. 호스트 또는 비밀번호를 확인해주세요."
        if [[ "$VERSION" =~ "Code:" ]]; then
            print_error "오류: ${VERSION}"
        fi
        print_error "다시 시작하려면 스크립트를 재실행하세요."
        exit 1
    fi

    echo ""

    # Save credentials to hidden file
    print_info "인증 정보를 안전하게 저장 중..."

    # Ensure CH_HOST doesn't have https:// prefix before saving
    CH_HOST=$(echo "$CH_HOST" | sed -E 's|^https?://||')

    cat > "$CREDENTIALS_FILE" << EOF
# ============================================================================
# CH2OTEL Credentials (DO NOT COMMIT TO VERSION CONTROL)
# Generated: $(date)
# ============================================================================

# ClickHouse Cloud Connection (CHC Exclusive - Fixed Values)
CH_HOST=${CH_HOST}
CH_PORT=8443
CH_USER=default
CH_PASSWORD=${CH_PASSWORD}
EOF

    chmod 600 "$CREDENTIALS_FILE"
    print_success "인증 정보가 안전하게 저장되었습니다: $CREDENTIALS_FILE"

    # Add to .gitignore
    if [ ! -f "${SCRIPT_DIR}/.gitignore" ]; then
        echo ".credentials" > "${SCRIPT_DIR}/.gitignore"
        echo "ch2otel.conf" >> "${SCRIPT_DIR}/.gitignore"
        echo "ch2otel-setup.sql" >> "${SCRIPT_DIR}/.gitignore"
        print_success ".gitignore 파일이 생성되었습니다."
    fi

    echo ""
fi

# ============================================================================
# Step 3: Check for existing configuration
# ============================================================================
SKIP_CONFIG=false
if [ -f "$CONFIG_FILE" ]; then
    print_warning "기존 설정 파일이 발견되었습니다."
    prompt_input "기존 설정을 사용하시겠습니까? (yes/no)" "yes" USE_EXISTING_CONFIG

    if [ "$USE_EXISTING_CONFIG" = "yes" ] || [ "$USE_EXISTING_CONFIG" = "y" ]; then
        source "$CONFIG_FILE"
        print_success "기존 설정을 불러왔습니다."
        echo ""
        print_info "현재 설정:"
        echo "  • Database: ${DATABASE_NAME}"
        echo "  • Refresh Interval: ${REFRESH_INTERVAL_MINUTES}분"
        echo "  • Lookback Interval: ${LOOKBACK_INTERVAL_MINUTES}분"
        echo "  • Data Retention: ${DATA_RETENTION_DAYS}일"
        echo ""
        SKIP_CONFIG=true
    fi
    echo ""
fi

# ============================================================================
# Step 4: Database Configuration
# ============================================================================
if [ "$SKIP_CONFIG" = false ]; then
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_header "Step 2: Database Configuration"
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Validate Database Name
    while true; do
        prompt_input "Database 이름" "ch2otel" DATABASE_NAME

        if [ -z "$DATABASE_NAME" ]; then
            print_error "Database 이름을 입력해주세요."
            continue
        fi

        # Check valid database name (alphanumeric and underscore only)
        if [[ ! "$DATABASE_NAME" =~ ^[a-zA-Z][a-zA-Z0-9_]*$ ]]; then
            print_error "Database 이름은 영문자로 시작하고 영문자, 숫자, 밑줄(_)만 사용할 수 있습니다."
            continue
        fi

        print_success "Database: ${DATABASE_NAME}"
        break
    done

    echo ""

    # ============================================================================
    # Step 5: Collection Configuration
    # ============================================================================
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_header "Step 3: Collection Configuration"
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Validate Refresh Interval
    while true; do
        prompt_input "RMV Refresh 주기 (분)" "10" REFRESH_INTERVAL_MINUTES

        if [ -z "$REFRESH_INTERVAL_MINUTES" ]; then
            print_error "Refresh 주기를 입력해주세요."
            continue
        fi

        # Check if it's a valid integer
        if ! [[ "$REFRESH_INTERVAL_MINUTES" =~ ^[0-9]+$ ]]; then
            print_error "Refresh 주기는 정수여야 합니다 (예: 10)."
            continue
        fi

        # Check if positive
        if [ "$REFRESH_INTERVAL_MINUTES" -le 0 ]; then
            print_error "Refresh 주기는 0보다 커야 합니다."
            continue
        fi

        # Warning for very short interval
        if [ "$REFRESH_INTERVAL_MINUTES" -lt 5 ]; then
            print_warning "경고: Refresh 주기가 5분 미만입니다. 시스템에 부하가 발생할 수 있습니다."
            prompt_input "계속하시겠습니까? (yes/no)" "no" CONFIRM_REFRESH
            if [ "$CONFIRM_REFRESH" != "yes" ] && [ "$CONFIRM_REFRESH" != "y" ]; then
                continue
            fi
        fi

        print_success "Refresh 주기: ${REFRESH_INTERVAL_MINUTES}분"
        break
    done

    # Calculate Lookback Interval (Refresh Interval + 5 minutes buffer)
    LOOKBACK_INTERVAL_MINUTES=$((REFRESH_INTERVAL_MINUTES + 5))
    print_info "Lookback Interval (자동 계산): ${LOOKBACK_INTERVAL_MINUTES}분"

    echo ""

    # ============================================================================
    # Step 6: Data Retention Configuration
    # ============================================================================
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_header "Step 4: Data Retention Configuration"
    print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Validate Data Retention Days
    while true; do
        prompt_input "OTEL 데이터 보관 기간 (일)" "30" DATA_RETENTION_DAYS

        if [ -z "$DATA_RETENTION_DAYS" ]; then
            print_error "데이터 보관 기간을 입력해주세요."
            continue
        fi

        # Check if it's a valid integer
        if ! [[ "$DATA_RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
            print_error "보관 기간은 정수여야 합니다 (예: 30)."
            continue
        fi

        # Check if positive
        if [ "$DATA_RETENTION_DAYS" -le 0 ]; then
            print_error "보관 기간은 0보다 커야 합니다."
            continue
        fi

        # Warning for very short retention
        if [ "$DATA_RETENTION_DAYS" -lt 7 ]; then
            print_warning "경고: 보관 기간이 7일 미만입니다. 데이터 분석에 충분하지 않을 수 있습니다."
            prompt_input "계속하시겠습니까? (yes/no)" "no" CONFIRM_DATA_RETENTION
            if [ "$CONFIRM_DATA_RETENTION" != "yes" ] && [ "$CONFIRM_DATA_RETENTION" != "y" ]; then
                continue
            fi
        fi

        print_success "데이터 보관 기간: ${DATA_RETENTION_DAYS}일"
        break
    done

    echo ""
fi

# ============================================================================
# Step 7: Generate Configuration File
# ============================================================================
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
print_header "Step 5: Generating Configuration"
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cat > "$CONFIG_FILE" << EOF
# ============================================================================
# CH2OTEL Configuration (Non-Sensitive)
# Generated: $(date)
# ============================================================================

# Database Configuration
DATABASE_NAME=${DATABASE_NAME}

# Collection Configuration
REFRESH_INTERVAL_MINUTES=${REFRESH_INTERVAL_MINUTES}
LOOKBACK_INTERVAL_MINUTES=${LOOKBACK_INTERVAL_MINUTES}

# Data Retention Configuration
DATA_RETENTION_DAYS=${DATA_RETENTION_DAYS}

# Connection Settings (CHC Exclusive)
CH_SECURE=true
CH_PORT=${CH_PORT}
EOF

chmod 644 "$CONFIG_FILE"
print_success "설정 파일 생성 완료: $CONFIG_FILE"
echo ""

# ============================================================================
# Step 8: Generate SQL Script
# ============================================================================
print_info "SQL 스크립트 생성 중..."

SQL_FILE="${SCRIPT_DIR}/ch2otel-setup.sql"

# Load credentials for SQL generation
source "$CREDENTIALS_FILE"

# Replace variables in SQL template
sed -e "s/\${DATABASE_NAME}/${DATABASE_NAME}/g" \
    -e "s/\${REFRESH_INTERVAL_MINUTES}/${REFRESH_INTERVAL_MINUTES}/g" \
    -e "s/\${LOOKBACK_INTERVAL_MINUTES}/${LOOKBACK_INTERVAL_MINUTES}/g" \
    -e "s/\${DATA_RETENTION_DAYS}/${DATA_RETENTION_DAYS}/g" \
    "${SCRIPT_DIR}/sql/ch2otel-template.sql" > "$SQL_FILE"

print_success "SQL 스크립트 생성 완료: $SQL_FILE"
echo ""

# ============================================================================
# Step 9: Connection Test & Installation
# ============================================================================
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
print_header "Step 6: Installation"
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Load credentials for connection test
if [ -f "$CREDENTIALS_FILE" ]; then
    source "$CREDENTIALS_FILE"
fi

# Check if clickhouse client is available
if [ -z "$CLICKHOUSE_CLIENT_CMD" ]; then
    print_error "clickhouse client가 설치되어 있지 않습니다."
    print_info "설치 방법: https://clickhouse.com/docs/en/install"
    exit 1
fi

# Build clickhouse client command
build_ch_client_cmd() {
    local cmd="${CLICKHOUSE_CLIENT_CMD}"
    cmd+=" --host=${CH_HOST}"
    cmd+=" --user=${CH_USER}"
    cmd+=" --password=${CH_PASSWORD}"
    cmd+=" --secure"
    echo "$cmd"
}

CH_CLIENT_FULL_CMD=$(build_ch_client_cmd)

# Test connection
print_info "ClickHouse Cloud 연결 테스트 중..."
if eval "$CH_CLIENT_FULL_CMD --query='SELECT version()'" > /dev/null 2>&1; then
    print_success "연결 테스트 성공!"
else
    print_error "연결 테스트 실패. 연결 정보를 확인해주세요."
    exit 1
fi

echo ""

# Execute SQL script
print_warning "SQL 스크립트를 실행하시겠습니까?"
print_warning "경고: 기존 ${DATABASE_NAME} 데이터베이스의 테이블과 뷰가 영향을 받을 수 있습니다."
echo ""
prompt_input "계속하시겠습니까? (yes/no)" "yes" CONFIRM_EXECUTE

if [ "$CONFIRM_EXECUTE" = "yes" ] || [ "$CONFIRM_EXECUTE" = "y" ]; then
    print_info "SQL 스크립트 실행 중..."

    # Execute SQL script
    if eval "$CH_CLIENT_FULL_CMD --multiquery < ${SQL_FILE}"; then
        print_success "SQL 스크립트 실행 완료!"
    else
        print_error "SQL 스크립트 실행 실패"
        exit 1
    fi
else
    print_info "SQL 스크립트 실행이 취소되었습니다."
    print_info "다음 명령어로 수동 실행할 수 있습니다:"
    echo ""
    echo "  source ${CREDENTIALS_FILE}"
    echo "  ${CLICKHOUSE_CLIENT_CMD} --host=\${CH_HOST} --user=\${CH_USER} --password=\${CH_PASSWORD} --secure --multiquery < ${SQL_FILE}"
    echo ""
    exit 0
fi

echo ""

# ============================================================================
# Step 10: Verification
# ============================================================================
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
print_header "Step 7: Verification"
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

print_info "설치 확인 중..."

# Check tables
TABLES_COUNT=$(eval "$CH_CLIENT_FULL_CMD --query=\"SELECT count() FROM system.tables WHERE database = '${DATABASE_NAME}'\" 2>/dev/null || echo 0")

if [ "$TABLES_COUNT" -gt 0 ]; then
    print_success "테이블 생성 확인: ${TABLES_COUNT}개 테이블 발견"

    # List tables
    echo ""
    print_info "생성된 테이블 목록:"
    eval "$CH_CLIENT_FULL_CMD --query=\"SELECT name, engine FROM system.tables WHERE database = '${DATABASE_NAME}' ORDER BY name FORMAT PrettyCompact\""
else
    print_warning "테이블이 생성되지 않았습니다."
fi

echo ""

# Check RMV status
print_info "Refreshable Materialized View 상태 확인 중..."
RMV_COUNT=$(eval "$CH_CLIENT_FULL_CMD --query=\"SELECT count() FROM system.view_refreshes WHERE database = '${DATABASE_NAME}'\" 2>/dev/null || echo 0")

if [ "$RMV_COUNT" -gt 0 ]; then
    print_success "RMV 확인: ${RMV_COUNT}개 RMV 발견"

    # Show RMV status
    echo ""
    print_info "RMV 상태:"
    eval "$CH_CLIENT_FULL_CMD --query=\"SELECT view, status, next_refresh_time FROM system.view_refreshes WHERE database = '${DATABASE_NAME}' FORMAT PrettyCompact\""
else
    print_warning "RMV가 발견되지 않았습니다."
fi

echo ""

# ============================================================================
# Step 11: Summary
# ============================================================================
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║              CH2OTEL 설치가 완료되었습니다!                  ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"
print_header "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

print_info "생성된 파일:"
echo "  • 인증 정보 (보안): ${CREDENTIALS_FILE} (권한: 600)"
echo "  • 설정 파일: ${CONFIG_FILE}"
echo "  • SQL 스크립트: ${SQL_FILE}"
echo ""

print_warning "보안 주의사항:"
echo "  • ${CREDENTIALS_FILE} 파일을 절대 Git에 커밋하지 마세요!"
echo "  • .gitignore에 자동으로 추가되었습니다."
echo ""

print_info "주요 객체:"
echo "  • Database: ${DATABASE_NAME}"
echo "  • Tables: otel_logs, otel_traces, otel_metrics_gauge, otel_metrics_sum, otel_metrics_histogram, hyperdx_sessions"
echo "  • RMVs: rmv_part_logs, rmv_mview_logs, rmv_status_logs"
echo "  • Refresh Interval: ${REFRESH_INTERVAL_MINUTES}분마다 자동 갱신"
echo ""

print_info "다음 단계:"
echo ""
echo "  1. 로그 확인:"
echo "     SELECT * FROM ${DATABASE_NAME}.otel_logs ORDER BY Timestamp DESC LIMIT 10;"
echo ""
echo "  2. 트레이스 확인:"
echo "     SELECT * FROM ${DATABASE_NAME}.otel_traces ORDER BY Timestamp DESC LIMIT 10;"
echo ""
echo "  3. 메트릭 확인:"
echo "     SELECT * FROM ${DATABASE_NAME}.otel_metrics_gauge ORDER BY TimeUnix DESC LIMIT 10;"
echo ""
echo "  4. RMV 상태 확인:"
echo "     SELECT * FROM system.view_refreshes WHERE database = '${DATABASE_NAME}';"
echo ""
echo "  5. RMV 수동 실행:"
echo "     SYSTEM REFRESH VIEW ${DATABASE_NAME}.rmv_part_logs;"
echo ""

print_success "CH2OTEL이 성공적으로 설치되었습니다!"
print_info "자세한 내용은 README.md 파일을 참조하세요."
echo ""
