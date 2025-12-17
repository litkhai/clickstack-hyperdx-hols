# O11y Vector AI 프로젝트 요약

## 생성된 파일 목록

### 핵심 문서
- README.md - 프로젝트 개요 및 사용 가이드
- SETUP.md - 상세 설정 가이드
- .env.example - 환경 변수 템플릿
- .gitignore - Git 제외 파일 설정
- docker-compose.yml - Docker Compose 설정

### Terraform (AWS 인프라)
- terraform/main.tf - 메인 인프라 정의
- terraform/variables.tf - 변수 정의
- terraform/outputs.tf - 출력 정의
- terraform/user-data.sh - EC2 초기화 스크립트
- terraform/terraform.tfvars.example - 변수 값 템플릿

### ClickHouse
- clickhouse/schemas/01_otel_tables.sql - OTEL 기본 테이블
- clickhouse/schemas/02_vector_tables.sql - Vector Search 테이블
- clickhouse/queries/similar_error_search.sql - 유사 에러 검색 쿼리
- clickhouse/queries/anomaly_detection.sql - 이상 탐지 쿼리

### Sample App (FastAPI)
- sample-app/Dockerfile
- sample-app/requirements.txt
- sample-app/main.py - 메인 애플리케이션
- sample-app/otel_config.py - OpenTelemetry 설정

### Data Generator
- data-generator/Dockerfile
- data-generator/requirements.txt
- data-generator/generator.py - 트래픽 생성기
- data-generator/config.yaml - 생성 패턴 설정

### Embedding Pipeline
- embedding-pipeline/Dockerfile
- embedding-pipeline/requirements.txt
- embedding-pipeline/batch_processor.py - Embedding 생성 파이프라인
- embedding-pipeline/config.yaml - 처리 설정

### HyperDX / OTEL Collector
- hyperdx/otel-collector-config.yaml - OTEL Collector 설정

### 배포 스크립트
- scripts/setup-clickhouse.sh - ClickHouse 스키마 생성
- scripts/deploy.sh - 로컬 배포
- scripts/terraform-deploy.sh - Terraform 배포
- scripts/setup-ec2.sh - EC2 인스턴스 설정

### AI Agent 연동 (선택사항)
- mcp/claude_desktop_config.json.example - MCP 설정 예시

## 주요 기능

1. **데이터 수집**
   - FastAPI 기반 E-commerce 샘플 애플리케이션
   - OpenTelemetry 자동 계측
   - OTEL Collector를 통한 ClickHouse Cloud 전송

2. **Vector Search**
   - OpenAI Embeddings (text-embedding-ada-002)
   - ClickHouse usearch Vector Index
   - 유사 에러 로그 검색
   - 이상 트레이스 탐지

3. **인프라 자동화**
   - Terraform으로 AWS EC2 자동 배포
   - Docker Compose로 로컬 개발 환경
   - 환경 변수 기반 설정 관리
   - Git에서 민감 정보 제외

4. **AI Agent 연동 (선택사항)**
   - MCP를 통한 ClickHouse 접근
   - 프롬프트 기반 자동 분석

## 배포 방법

### 로컬 개발
1. .env 파일 설정
2. ./scripts/setup-clickhouse.sh
3. ./scripts/deploy.sh

### AWS EC2 배포
1. terraform.tfvars 설정
2. 환경 변수 export (비밀번호, API 키)
3. ./scripts/terraform-deploy.sh
4. EC2 접속 후 ./scripts/setup-ec2.sh

## 보안 고려사항
- 모든 민감한 정보는 .gitignore에 포함
- Terraform 상태 파일도 제외됨
- 환경 변수로 비밀번호 전달
- EC2 Security Group으로 접근 제어

## 예상 비용
- 데모 기간(1-2주): 약 $100 이내
- EC2 t3.large, ClickHouse Cloud Development tier, OpenAI Embeddings 포함
