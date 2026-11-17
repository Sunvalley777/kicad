# OrCAD → KiCad 회로도 변환 결과 (ex1)

대상: ZBA_FW086_V2_240503 (LA123WFE-SL04 NEW CNT BOARD V2), OrCAD Capture 25.1
변환일: 2026-11-17

## 결과 요약

| 항목 | 결과 |
| --- | --- |
| 부품 | 343개 전부 변환 (미실장 OPEN 54개는 KiCad DNP로 표시) |
| 연결(넷) | **EDIF 원본 넷 309개와 KiCad 넷 309개 100% 일치** (핀 누락 0) |
| 배선 | 연결선 1,442개, 접점 264개, 미사용 핀(X) 66개, 넷 라벨 404개 (OrCAD 원래 위치) |
| 전원·페이지 연결자 | 전원 심볼 134개, 오프페이지 연결자 129개 → 글로벌 라벨 |
| 배치 | OrCAD 원본과 같은 위치·방향 (A1 1페이지) |
| 메모 문구 | 15개 (예: SILK : UART) |

연결 검증 방법: EDIF의 넷별 (부품, 핀) 묶음을 정답으로 두고,
KiCad에서 다시 추출한 넷리스트와 묶음 단위로 비교했습니다 (`compare_result.txt`).
검증은 KiCad 7 명령줄 도구로 했습니다. KiCad 10에서 열면 파일 형식 업그레이드 안내가 나올 수 있으며, 저장하면 됩니다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `ZBA_FW086_V2_240503.kicad_pro` / `.kicad_sch` | KiCad 프로젝트와 회로도 (심볼은 회로도 안에 포함되어 별도 라이브러리 불필요) |
| `KiCad_preview.pdf` | 변환 결과를 KiCad로 출력한 PDF (원본 PDF와 나란히 비교용) |
| `ZBA_FW086_V2_240503.edif_netlist.json` | EDIF 기준 정답 넷리스트 |
| `compare_result.txt` | 넷 비교 결과 |
| `tools/` | 변환·검증 스크립트 (다른 보드에도 재사용) |

## 남은 작업 / 한계

1. **풋프린트 미연결**: OrCAD 풋프린트 이름(예: 1608C, 2012L)은 각 부품의 `OrCAD_Footprint` 필드에 보관했습니다. KiCad 풋프린트 매핑표를 만들어야 PCB로 넘어갈 수 있습니다.
2. **심볼은 방향별로 따로 생성**: 위치·연결 정확도를 위해 회전·반전된 부품마다 심볼이 따로 있습니다 (71개). 이후 Design Block(베이스 모델)으로 정리할 때 통합하면 됩니다.
3. **도면 테두리·타이틀 블록**은 옮기지 않았습니다 (KiCad 기본 타이틀 블록 사용).
4. 일부 수동 부품(CAPACITOR 심볼)은 핀 번호가 보일 수 있습니다 (표시만의 차이).
5. KiCad 10에서 ERC를 실행해 경고를 한 번 확인하세요.

## 다른 OrCAD 보드에 쓰는 방법

OrCAD Capture에서 같은 회로도를 **EDIF(.EDF)** 와 **XML** 로 내보낸 뒤:

```
python tools/convert.py 보드.EDF 보드.xml 출력.kicad_sch
kicad-cli sch export netlist -o 출력.net 출력.kicad_sch
python tools/compare.py 출력.edif_netlist.json 출력.net
```

`exact match` 수와 EDIF 넷 수가 같고 `mismatch: 0`이면 연결이 원본과 같습니다.
처리 위치는 사내 PC이며, 외부 서버로 회로도를 보내지 않습니다.
