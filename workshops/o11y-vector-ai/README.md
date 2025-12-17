# ClickHouse Agentic AI & Vector Search in Observability

ClickHouse Vector Search와 Agentic AI를 활용한 Observability 데모 프로젝트입니다.

## 개요

이 프로젝트는 다음 기능을 제공합니다:

- **유사 에러 로그 검색**: Vector Index를 사용하여 과거 유사 에러 패턴 탐색
- **이상 트레이스 탐지**: Embedding 기반 정상/비정상 패턴 분류
- **AI 기반 분석**: AI Agent가 ClickHouse를 쿼리하여 근본 원인 자동 분석 (선택사항)

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWS EC2 Instance                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Sample App     │  │  Data Generator │  │  Embedding      │ │
│  │  (FastAPI)      │  │  (Traffic Gen)  │  │  Pipeline       │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                ▼                                │
│                    ┌─────────────────────┐                      │
│                    │  OTEL Collector     │                      │
│                    └──────────┬──────────┘                      │
└───────────────────────────────┼─────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                      ClickHouse Cloud                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐│
│  │ otel_logs    │  │ otel_traces  │  │ logs_with_embeddings     ││
│  └──────────────┘  └──────────────┘  └──────────────────────────┘│
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  traces_with_embeddings + Vector Index (usearch)             ││
│  └──────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────┘
```

## 사전 요구사항

### 로컬 개발 환경
- Docker & Docker Compose
- Python 3.11+
- ClickHouse CLI (선택사항)

### 클라우드 리소스
- AWS 계정 (EC2 배포용)
- ClickHouse Cloud 인스턴스
- OpenAI API 키

## 빠른 시작 (로컬)

### 1. 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 수정하여 다음 정보를 입력:
```bash
# ClickHouse Cloud 연결 정보 (HTTPS/TLS 필수)
CLICKHOUSE_HOST=your-instance.clickhouse.cloud  # 예: abc123.us-east-1.aws.clickhouse.cloud
CLICKHOUSE_PORT=8443                              # ClickHouse Cloud HTTPS 포트
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your-password
CLICKHOUSE_DB=o11y
CLICKHOUSE_SECURE=true                            # TLS/SSL 활성화 (필수)

# OpenAI API 키
OPENAI_API_KEY=sk-your-api-key
```

**중요**: ClickHouse Cloud는 **포트 8443 (HTTPS)**을 사용하며 TLS/SSL이 필수입니다.

### 2. ClickHouse 스키마 생성

```bash
cd scripts
./setup-clickhouse.sh
```

### 3. 서비스 시작

```bash
./deploy.sh
```

### 4. 서비스 확인

- Sample App: http://localhost:8000
- OTEL Collector gRPC: localhost:4317
- OTEL Collector HTTP: localhost:4318

로그 확인:
```bash
docker-compose logs -f
```

## AWS EC2 배포

### 1. Terraform 설정

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` 파일 수정:
```hcl
aws_region = "ap-northeast-2"
environment = "demo"

# User-defined Tags
user_name    = "Your Name"
user_contact = "your.email@example.com"
application  = "o11y-vector-ai-demo"
end_date     = "2025-12-31"

# Network Configuration
vpc_id    = "vpc-xxxxxxxxx"
subnet_id = "subnet-xxxxxxxxx"

# EC2 Configuration
ami_id        = "ami-086cae3329a3f7d75"  # Ubuntu 22.04 in Seoul
instance_type = "t3.large"

# SSH Configuration
ssh_public_key = "ssh-rsa AAAAB3Nza... your-key"

# ClickHouse Cloud Configuration
clickhouse_host = "your-instance.clickhouse.cloud"
clickhouse_user = "default"
clickhouse_db   = "o11y"
```

### 2. 환경 변수 설정

민감한 정보는 환경 변수로 설정:
```bash
export TF_VAR_clickhouse_password="your-clickhouse-password"
export TF_VAR_openai_api_key="your-openai-api-key"
```

### 3. AWS 인증

```bash
# AWS Configure 사용
aws configure

# 또는 환경 변수
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_SESSION_TOKEN="your-session-token"  # 필요시
```

### 4. Terraform 배포

```bash
cd scripts
./terraform-deploy.sh
```

### 5. EC2 인스턴스 접속 및 설정

```bash
# Terraform output에서 표시된 SSH 명령어 사용
ssh -i your-private-key.pem ubuntu@<EC2-PUBLIC-IP>

# EC2에서 설정 스크립트 실행
cd /home/ubuntu/o11y-vector-ai
./scripts/setup-ec2.sh
```

## 프로젝트 구조

```
o11y-vector-ai/
├── README.md
├── .env.example                    # 환경 변수 템플릿
├── .gitignore
├── docker-compose.yml              # 로컬 Docker Compose 설정
│
├── terraform/                      # AWS 인프라 코드
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── user-data.sh
│   └── terraform.tfvars.example
│
├── clickhouse/                     # ClickHouse 스키마 및 쿼리
│   ├── schemas/
│   │   ├── 01_otel_tables.sql     # OTEL 기본 테이블
│   │   └── 02_vector_tables.sql   # Vector Search 테이블
│   └── queries/
│       ├── similar_error_search.sql
│       └── anomaly_detection.sql
│
├── sample-app/                     # FastAPI E-commerce 샘플 앱
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── otel_config.py
│
├── data-generator/                 # 트래픽 생성기
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── generator.py
│   └── config.yaml
│
├── embedding-pipeline/             # Embedding 처리 파이프라인
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── batch_processor.py
│   └── config.yaml
│
├── hyperdx/                        # HyperDX 및 OTEL Collector 설정
│   └── otel-collector-config.yaml
│
└── scripts/                        # 배포 및 설정 스크립트
    ├── setup-clickhouse.sh
    ├── deploy.sh
    ├── terraform-deploy.sh
    └── setup-ec2.sh
```

## Vector Search 쿼리 예시

### 유사 에러 검색

```sql
WITH current_error AS (
    SELECT embedding
    FROM o11y.logs_with_embeddings
    WHERE TraceId = 'your-trace-id'
    LIMIT 1
)
SELECT
    l.Timestamp,
    l.ServiceName,
    l.Body,
    cosineDistance(l.embedding, ce.embedding) AS distance
FROM o11y.logs_with_embeddings l
CROSS JOIN current_error ce
WHERE l.SeverityText = 'ERROR'
    AND cosineDistance(l.embedding, ce.embedding) < 0.3
ORDER BY distance ASC
LIMIT 10;
```

### 이상 트레이스 탐지

```sql
WITH normal_centroid AS (
    SELECT arrayMap(i -> avg(embedding[i]), range(1, 1537)) AS centroid
    FROM o11y.traces_with_embeddings
    WHERE is_anomaly = 0
)
SELECT
    TraceId,
    span_sequence,
    total_duration,
    error_count,
    cosineDistance(embedding, nc.centroid) AS anomaly_score
FROM o11y.traces_with_embeddings t, normal_centroid nc
WHERE Timestamp > now() - INTERVAL 10 MINUTE
ORDER BY anomaly_score DESC
LIMIT 20;
```

## AI Agent 활용 (선택사항)

Claude 또는 다른 AI Agent에게 ClickHouse 데이터베이스 접근 권한을 부여하여 자동 분석을 수행할 수 있습니다.

**MCP 설정 예시**는 `mcp/claude_desktop_config.json.example` 파일을 참조하세요.

## AI Agent 프롬프트 예시

ClickHouse 데이터베이스에 접근 가능한 AI Agent에게 다음과 같은 프롬프트를 사용할 수 있습니다:

### 에러 근본 원인 분석

```
지난 1시간 동안 payment 서비스에서 에러가 급증했습니다.
o11y 데이터베이스에 있는 otel_logs와 otel_traces 테이블을 분석하여:

1. 에러 현황 파악 (에러 수, 에러 유형)
2. 가장 빈번한 에러 메시지 분석
3. logs_with_embeddings 테이블에서 Vector Search를 사용하여 유사한 과거 에러 패턴 검색
4. 연관된 트레이스 분석 (otel_traces 테이블)
5. 근본 원인 추론 및 해결 방법 제안

Vector Search 쿼리는 clickhouse/queries/similar_error_search.sql을 참고하세요.
```

### 이상 트레이스 탐지

```
현재 시스템에서 비정상적인 패턴이 있는지 확인해주세요.
o11y 데이터베이스의 traces_with_embeddings 테이블을 사용하여:

1. 최근 10분간 트레이스의 이상 점수 계산 (정상 패턴 centroid 대비)
2. anomaly_score가 높은 트레이스 상세 분석
3. 이상 패턴의 특징 요약 (높은 latency, 많은 에러, 비정상적인 span 시퀀스 등)
4. 영향을 받는 서비스 및 사용자 세션 확인

쿼리 예시는 clickhouse/queries/anomaly_detection.sql을 참고하세요.
```

### 유사 에러 검색

```
최근 발생한 "Payment Gateway timeout" 에러와 유사한 과거 에러를 찾아주세요.
logs_with_embeddings 테이블에서 Vector Search를 수행하여:

1. 해당 에러와 코사인 거리가 가까운 (< 0.3) 과거 로그 검색
2. 유사 에러들의 공통 패턴 분석
3. error_patterns 테이블에서 매칭되는 알려진 패턴 조회
4. 과거 해결 방법 (resolution) 제안
```

## 문제 해결

### ClickHouse 연결 오류

```bash
# 연결 테스트
clickhouse-client \
    --host="your-instance.clickhouse.cloud" \
    --port=8443 \
    --user=default \
    --password="your-password" \
    --secure \
    --query="SELECT 1"
```

### Docker 컨테이너 로그 확인

```bash
# 모든 서비스 로그
docker-compose logs -f

# 특정 서비스 로그
docker-compose logs -f sample-app
docker-compose logs -f data-generator
docker-compose logs -f embedding-pipeline
```

### OTEL Collector 상태 확인

```bash
# Metrics 확인
curl http://localhost:8888/metrics
```

## 정리

### 로컬 환경

```bash
docker-compose down -v
```

### AWS 리소스

```bash
cd terraform
terraform destroy
```

## 예상 비용

- **AWS EC2 (t3.large)**: ~$60/월
- **ClickHouse Cloud (Development)**: ~$200/월 (데모 기간만 사용)
- **OpenAI Embeddings (ada-002)**: ~$10-20 (데모 데이터 기준)

**데모 기간(1-2주) 총 예상 비용**: ~$100 이내

## 보안 고려사항

- `.env` 파일은 절대 Git에 커밋하지 마세요
- Terraform 상태 파일에 민감한 정보가 포함될 수 있으므로 주의
- EC2 보안 그룹에서 SSH 접근을 특정 IP로 제한하세요
- 사용 후 모든 리소스를 정리하세요

## 라이선스

이 프로젝트는 데모 목적으로 제공됩니다.

## 참고 자료

- [ClickHouse Vector Search Documentation](https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/annindexes)
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [HyperDX](https://www.hyperdx.io/)
- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/)
