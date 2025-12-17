# ClickHouse Cloud 연결 설정 가이드

## 개요

이 프로젝트는 **ClickHouse Cloud**를 데이터 저장소로 사용합니다. 로컬 개발 환경과 EC2 배포 환경 모두 ClickHouse Cloud에 연결됩니다.

## ClickHouse Cloud 연결 정보

### 필수 설정값

| 설정 | 값 | 설명 |
|------|-----|------|
| **Host** | `xxx.clickhouse.cloud` | 인스턴스 호스트명 |
| **Port** | `8443` | HTTPS 포트 (필수) |
| **Protocol** | HTTPS/TLS | SSL/TLS 암호화 필수 |
| **User** | `default` | 기본 사용자 |
| **Password** | (생성된 비밀번호) | ClickHouse Cloud에서 발급 |
| **Database** | `o11y` | 프로젝트 데이터베이스 |

### 환경 변수 설정

```bash
# .env 파일
CLICKHOUSE_HOST=abc123.us-east-1.aws.clickhouse.cloud
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your-secure-password
CLICKHOUSE_DB=o11y
CLICKHOUSE_SECURE=true
```

## 포트 및 프로토콜

### ❌ 잘못된 설정

```bash
# 로컬 ClickHouse 설정 (사용하지 않음)
CLICKHOUSE_PORT=9000          # Native TCP 포트
CLICKHOUSE_SECURE=false       # 비암호화
```

이 설정은 **로컬 ClickHouse 서버**용이며, **ClickHouse Cloud에서는 작동하지 않습니다**.

### ✅ 올바른 설정

```bash
# ClickHouse Cloud 설정
CLICKHOUSE_PORT=8443          # HTTPS 포트
CLICKHOUSE_SECURE=true        # TLS/SSL 필수
```

## 연결 테스트

### CLI로 테스트

```bash
clickhouse-client \
    --host=your-instance.clickhouse.cloud \
    --port=8443 \
    --user=default \
    --password="your-password" \
    --secure \
    --query="SELECT version(), currentDatabase()"
```

**성공 시 출력 예시**:
```
24.10.1.2812    o11y
```

### Python으로 테스트

```python
import clickhouse_connect

client = clickhouse_connect.get_client(
    host='your-instance.clickhouse.cloud',
    port=8443,
    username='default',
    password='your-password',
    database='o11y',
    secure=True  # TLS/SSL 필수
)

result = client.query('SELECT version()')
print(result.result_rows)
```

## OTEL Collector 설정

### 올바른 설정

`hyperdx/otel-collector-config.yaml`:

```yaml
exporters:
  clickhouse/logs:
    endpoint: https://${CLICKHOUSE_HOST}:${CLICKHOUSE_PORT}
    database: ${CLICKHOUSE_DB}
    logs_table_name: otel_logs
    tls:
      insecure: false
      insecure_skip_verify: false
    username: ${CLICKHOUSE_USER}
    password: ${CLICKHOUSE_PASSWORD}
```

**주요 포인트**:
- `https://` 프로토콜 사용
- 포트 `${CLICKHOUSE_PORT}` (8443)
- TLS 설정: `insecure: false`
- 인증 정보 포함

## 자주 발생하는 오류

### 1. Connection Refused

**원인**: 잘못된 포트 (9000 대신 8443)

**해결**:
```bash
# .env 파일 확인
cat .env | grep CLICKHOUSE_PORT

# 8443이어야 함
CLICKHOUSE_PORT=8443
```

### 2. SSL Handshake Failed

**원인**: `--secure` 플래그 없음 또는 `CLICKHOUSE_SECURE=false`

**해결**:
```bash
# CLI에서 --secure 플래그 추가
clickhouse-client --secure ...

# 환경 변수
CLICKHOUSE_SECURE=true
```

### 3. Authentication Failed

**원인**: 잘못된 비밀번호 또는 사용자명

**해결**:
```bash
# ClickHouse Cloud 콘솔에서 비밀번호 재확인
# 특수문자가 있으면 따옴표로 감싸기
CLICKHOUSE_PASSWORD="your-password-with-special@chars"
```

### 4. Database Not Found

**원인**: 데이터베이스 `o11y`가 생성되지 않음

**해결**:
```bash
# 스키마 생성 스크립트 실행
cd scripts
./setup-clickhouse.sh
```

## ClickHouse Cloud vs 로컬 ClickHouse

| 항목 | ClickHouse Cloud | 로컬 ClickHouse |
|------|-----------------|----------------|
| **포트** | 8443 (HTTPS) | 9000 (Native TCP) |
| **프로토콜** | HTTPS/TLS | Native TCP |
| **SSL/TLS** | 필수 | 선택사항 |
| **관리** | 완전 관리형 | 직접 관리 |
| **Vector Search** | 지원 | 설정 필요 |
| **비용** | 사용량 기반 | 인프라 비용 |

## 보안 고려사항

### 1. 비밀번호 보호

```bash
# ❌ 절대 Git에 커밋하지 마세요
git add .env

# ✅ .gitignore에 포함됨
.env
*.env
```

### 2. 환경 변수로 전달

```bash
# Terraform 배포 시
export TF_VAR_clickhouse_password="your-password"

# Docker Compose는 .env 파일 자동 로드
docker-compose up -d
```

### 3. IP 화이트리스트

ClickHouse Cloud 콘솔에서:
1. Settings → Security
2. IP Access List 설정
3. 필요한 IP만 허용 (또는 "Anywhere" 개발용)

## 추가 리소스

- [ClickHouse Cloud Documentation](https://clickhouse.com/docs/en/cloud/overview)
- [ClickHouse Client CLI](https://clickhouse.com/docs/en/integrations/sql-clients/cli)
- [clickhouse-connect Python Library](https://clickhouse.com/docs/en/integrations/python)

## 체크리스트

배포 전 확인사항:

- [ ] ClickHouse Cloud 서비스가 실행 중인가?
- [ ] 호스트명이 `.clickhouse.cloud`로 끝나는가?
- [ ] 포트가 `8443`인가?
- [ ] `CLICKHOUSE_SECURE=true`로 설정되어 있는가?
- [ ] 비밀번호가 정확한가?
- [ ] 데이터베이스 `o11y`가 생성되었는가?
- [ ] CLI로 연결 테스트를 했는가?
- [ ] `.env` 파일이 `.gitignore`에 포함되어 있는가?

모든 항목이 체크되면 배포를 진행하세요!
