# 설정 가이드

## 단계별 설정

### 1. ClickHouse Cloud 설정

1. [ClickHouse Cloud](https://clickhouse.cloud/)에 접속하여 계정 생성
2. 새 서비스 생성:
   - Region: Asia Pacific (Seoul) 또는 원하는 리전
   - Tier: Development (데모용)
3. 접속 정보 확인:
   - Host: `xxx.clickhouse.cloud` (예: `abc123.us-east-1.aws.clickhouse.cloud`)
   - Port: `8443` (**HTTPS 포트 - 필수**)
   - User: `default`
   - Password: (생성된 비밀번호 복사)

**중요 사항**:
- ClickHouse Cloud는 **TLS/SSL이 필수**입니다
- 포트 8443 (HTTPS)을 사용합니다
- 포트 9000 (TCP)는 사용하지 않습니다
- 모든 연결은 암호화됩니다

### 2. OpenAI API 키 발급

1. [OpenAI Platform](https://platform.openai.com/)에 로그인
2. API Keys 메뉴에서 새 키 생성
3. 키를 안전한 곳에 저장

### 3. AWS 설정 (EC2 배포 시)

#### VPC 및 Subnet 확인

```bash
# VPC 목록 조회
aws ec2 describe-vpcs --region ap-northeast-2

# Subnet 목록 조회 (VPC ID 확인 후)
aws ec2 describe-subnets \
    --region ap-northeast-2 \
    --filters "Name=vpc-id,Values=vpc-xxxxxxxxx"
```

#### SSH 키 생성

```bash
# SSH 키 페어 생성 (로컬에서)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/o11y-vector-ai -C "o11y-demo"

# 공개 키 확인
cat ~/.ssh/o11y-vector-ai.pub
```

### 4. 환경 변수 설정

#### 로컬 개발

```bash
# .env 파일 생성
cp .env.example .env

# .env 파일 편집
nano .env
```

```bash
# ClickHouse Cloud 정보 입력
CLICKHOUSE_HOST=your-instance.clickhouse.cloud
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your-secure-password
CLICKHOUSE_DB=o11y

# OpenAI API 키 입력
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
```

#### Terraform 배포

```bash
# terraform.tfvars 파일 생성
cd terraform
cp terraform.tfvars.example terraform.tfvars

# terraform.tfvars 파일 편집
nano terraform.tfvars
```

```hcl
# AWS 및 네트워크 정보 입력
aws_region = "ap-northeast-2"
vpc_id     = "vpc-xxxxxxxxx"
subnet_id  = "subnet-xxxxxxxxx"

# 사용자 태그 정보
user_name    = "홍길동"
user_contact = "gildong@example.com"
application  = "o11y-vector-ai-demo"
end_date     = "2025-12-31"

# SSH 공개 키 (cat ~/.ssh/o11y-vector-ai.pub의 내용)
ssh_public_key = "ssh-rsa AAAAB3NzaC1yc2E..."

# ClickHouse 정보
clickhouse_host = "your-instance.clickhouse.cloud"
clickhouse_user = "default"
clickhouse_db   = "o11y"
```

민감한 정보는 환경 변수로:
```bash
export TF_VAR_clickhouse_password="your-password"
export TF_VAR_openai_api_key="sk-proj-xxxxxxxxxxxx"
```

### 5. ClickHouse 스키마 생성

```bash
cd scripts
chmod +x *.sh
./setup-clickhouse.sh
```

스키마가 정상 생성되었는지 확인:
```bash
clickhouse-client \
    --host="your-instance.clickhouse.cloud" \
    --port=8443 \
    --user=default \
    --password="your-password" \
    --secure \
    --query="SHOW TABLES FROM o11y"
```

예상 출력:
```
error_patterns
logs_with_embeddings
otel_logs
otel_sessions
otel_traces
session_replay_events
traces_with_embeddings
```

### 6. 로컬 배포

```bash
cd ..
./scripts/deploy.sh
```

서비스 상태 확인:
```bash
docker-compose ps
curl http://localhost:8000/health
```

### 7. Terraform EC2 배포

```bash
cd scripts
./terraform-deploy.sh
```

배포 후 출력된 정보 확인:
```
ec2_public_ip = "xx.xx.xx.xx"
sample_app_url = "http://xx.xx.xx.xx:8000"
ssh_command = "ssh -i <your-private-key> ubuntu@xx.xx.xx.xx"
```

EC2 접속 및 설정:
```bash
# SSH 접속
ssh -i ~/.ssh/o11y-vector-ai ubuntu@xx.xx.xx.xx

# User data 로그 확인
sudo tail -f /var/log/user-data.log

# 완료 확인 (완료되면 파일이 생성됨)
ls /var/log/user-data-complete

# 설정 스크립트 실행
cd /home/ubuntu/o11y-vector-ai
./scripts/setup-ec2.sh
```

### 8. AI Agent 연동 (선택사항)

AI Agent에게 ClickHouse 데이터베이스 접근 권한을 부여하여 자동 분석을 활용할 수 있습니다.

MCP (Model Context Protocol) 설정 예시는 `mcp/claude_desktop_config.json.example` 파일을 참조하세요.

자세한 AI Agent 활용법은 README.md의 "AI Agent 프롬프트 예시" 섹션을 참조하세요.

## 검증

### ClickHouse Cloud 연결 테스트

배포 전에 ClickHouse Cloud 연결을 먼저 테스트하세요:

```bash
# ClickHouse CLI로 연결 테스트 (포트 8443, --secure 필수)
clickhouse-client \
    --host="your-instance.clickhouse.cloud" \
    --port=8443 \
    --user=default \
    --password="your-password" \
    --secure \
    --query="SELECT 1"

# 성공 시 출력: 1
```

**연결 실패 시 체크리스트**:
- [ ] 호스트명이 정확한가? (예: `abc123.us-east-1.aws.clickhouse.cloud`)
- [ ] 포트가 8443인가?
- [ ] `--secure` 플래그를 사용했는가?
- [ ] 비밀번호가 정확한가?
- [ ] ClickHouse Cloud 서비스가 실행 중인가?
- [ ] 방화벽에서 8443 포트가 허용되는가?

### 데이터 생성 확인

```sql
-- 로그 확인
SELECT count() FROM o11y.otel_logs;

-- 트레이스 확인
SELECT count() FROM o11y.otel_traces;

-- Embedding 확인 (몇 분 후)
SELECT count() FROM o11y.logs_with_embeddings;
SELECT count() FROM o11y.traces_with_embeddings;
```

### Vector Search 테스트

```sql
-- 최근 에러 로그 확인
SELECT
    Timestamp,
    ServiceName,
    Body,
    length(embedding) as embedding_dim
FROM o11y.logs_with_embeddings
WHERE SeverityText = 'ERROR'
ORDER BY Timestamp DESC
LIMIT 5;
```

### 애플리케이션 테스트

```bash
# 제품 목록 조회
curl http://localhost:8000/products

# 특정 제품 조회
curl http://localhost:8000/products/1

# Health Check
curl http://localhost:8000/health
```

## 문제 해결

### ClickHouse 연결 실패

**증상**: `Connection refused`, `SSL handshake failed`, `Authentication failed`

**해결 방법**:

1. **포트 및 프로토콜 확인**:
   ```bash
   # ❌ 잘못된 설정 (포트 9000, TCP)
   CLICKHOUSE_PORT=9000

   # ✅ 올바른 설정 (포트 8443, HTTPS)
   CLICKHOUSE_PORT=8443
   CLICKHOUSE_SECURE=true
   ```

2. **연결 테스트**:
   ```bash
   # --secure 플래그 필수!
   clickhouse-client \
       --host=your-instance.clickhouse.cloud \
       --port=8443 \
       --user=default \
       --password=your-password \
       --secure \
       --query="SELECT version()"
   ```

3. **OTEL Collector 로그 확인**:
   ```bash
   docker-compose logs otel-collector | grep -i error
   # TLS/SSL 에러가 있는지 확인
   ```

4. **방화벽 확인**:
   - ClickHouse Cloud에서 IP 화이트리스트 확인
   - EC2 보안 그룹 아웃바운드 규칙에서 포트 8443 허용 확인
   - 로컬 방화벽에서 포트 8443 허용 확인

5. **환경 변수 확인**:
   ```bash
   # .env 파일 확인
   cat .env | grep CLICKHOUSE

   # Docker 컨테이너 내부 환경 변수 확인
   docker-compose exec otel-collector env | grep CLICKHOUSE
   ```

### Docker 컨테이너 시작 실패

```bash
# 로그 확인
docker-compose logs sample-app
docker-compose logs otel-collector

# 컨테이너 재시작
docker-compose restart

# 완전 재시작
docker-compose down
docker-compose up -d
```

### Embedding이 생성되지 않음

1. OpenAI API 키 확인:
   ```bash
   docker-compose logs embedding-pipeline
   ```

2. ClickHouse 연결 확인:
   ```bash
   docker-compose exec embedding-pipeline python -c "import clickhouse_connect; print('OK')"
   ```

3. API 할당량 확인:
   - OpenAI 대시보드에서 API 사용량 확인

### Terraform 배포 실패

```bash
# Terraform 상태 확인
cd terraform
terraform state list

# 특정 리소스 재생성
terraform taint aws_instance.o11y_server
terraform apply

# 완전 재배포
terraform destroy
terraform apply
```

## 리소스 정리

### 로컬 환경

```bash
# Docker 컨테이너 중지 및 삭제
docker-compose down -v

# 이미지 삭제
docker-compose down --rmi all
```

### AWS 리소스

```bash
cd terraform
terraform destroy
```

주의: 다음 리소스들을 확인하세요:
- EC2 인스턴스
- S3 버킷
- Security Group
- IAM Role
- Key Pair

### ClickHouse Cloud

ClickHouse Cloud 콘솔에서 서비스 삭제

## 추가 참고 자료

- [ClickHouse Vector Search](https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/annindexes)
- [OpenTelemetry Collector Configuration](https://opentelemetry.io/docs/collector/configuration/)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
