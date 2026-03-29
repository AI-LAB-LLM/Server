# from sqlalchemy import create_engine, text


# engine = create_engine("sqlite:///./db/protectee.db")

# query_template = text("""
# WITH
# params AS (
#     SELECT :name AS name, :date AS date
# ),
# max_stress_data AS (
#     SELECT e2.protectee_id, e2.stress AS max_stress, e2.timestamp AS max_stress_time
#     FROM event e2
#     JOIN users u2 ON e2.protectee_id = u2.id
#     JOIN params p ON u2.name = p.name
#         AND e2.timestamp LIKE p.date || '%'
#     ORDER BY e2.stress DESC, e2.timestamp ASC
#     LIMIT 1
# ),
# user_events AS (
#     SELECT e.*
#     FROM event e
#     JOIN max_stress_data msd ON e.protectee_id = msd.protectee_id
#     WHERE e.timestamp LIKE (SELECT date || '%' FROM params)
#     ORDER BY e.timestamp
# ),
# status_changes AS (
#     SELECT
#         ue.protectee_id,
#         ue.timestamp AS ts,
#         ue.is_watch_connected AS status,
#         CASE
#             WHEN ue.is_watch_connected = 0 
#                  AND LAG(ue.is_watch_connected) OVER (PARTITION BY ue.protectee_id ORDER BY ue.timestamp) = 1
#             THEN 1 ELSE 0 END AS is_disconnect,
#         CASE
#             WHEN ue.is_watch_connected = 1
#                  AND LAG(ue.is_watch_connected) OVER (PARTITION BY ue.protectee_id ORDER BY ue.timestamp) = 0
#             THEN 1 ELSE 0 END AS is_reconnect
#     FROM user_events ue
# ),
# watch_transitions AS (
#     SELECT 
#         d.protectee_id,
#         d.ts AS disconnect_time,
#         r.ts AS reconnect_time
#     FROM status_changes d
#     LEFT JOIN status_changes r
#         ON d.protectee_id = r.protectee_id
#         AND r.ts > d.ts
#         AND r.is_reconnect = 1
#     WHERE d.is_disconnect = 1
# ),
# filtered AS (
#     SELECT *
#     FROM user_events
#     WHERE
#         ppg_threat_detected >= 80 OR
#         imu_danger_level >= 4 OR
#         hrv >= 120 OR hrv <= 40 OR
#         stress >= 80 OR
#         zone_type = 'unfamiliar' OR
#         is_watch_connected = 0
# )
# SELECT
#     u.name,
#     p.date,

#     -- 조건별 이벤트 "고유 시점" 기준 카운트
#     COUNT(DISTINCT CASE WHEN f.ppg_threat_detected >= 80 THEN f.timestamp END) AS threat_count,
#     COUNT(DISTINCT CASE WHEN f.imu_danger_level >= 4 THEN f.timestamp END) AS imu_count,
#     COUNT(DISTINCT CASE WHEN f.hrv >= 120 OR f.hrv <= 40 THEN f.timestamp END) AS hrv_count,
#     COUNT(DISTINCT CASE WHEN f.stress >= 80 THEN f.timestamp END) AS stress_count,
#     COUNT(DISTINCT CASE WHEN f.zone_type = 'unfamiliar' THEN f.timestamp END) AS unfamiliar_count,

#     msd.max_stress,
#     msd.max_stress_time,

#     -- 타임라인들 (중복 제거 + 세미콜론 구분)
#     REPLACE(GROUP_CONCAT(DISTINCT
#         CASE WHEN f.ppg_threat_detected >= 80
#              THEN f.timestamp || '|' || f.ppg_threat_detected || '|' || f.zone_type
#         END
#     ), ',', ';') AS ppg_event_group,

#     REPLACE(GROUP_CONCAT(DISTINCT
#         CASE WHEN f.imu_danger_level >= 4
#              THEN f.timestamp || '|' || f.imu_danger_level || '|' || f.zone_type
#         END
#     ), ',', ';') AS imu_event_group,

#     REPLACE(GROUP_CONCAT(DISTINCT
#         CASE WHEN f.hrv >= 120 OR f.hrv<=40
#              THEN f.timestamp || '|' || f.hrv || '|' || f.zone_type
#         END
#     ), ',', ';') AS hrv_event_group,

#     REPLACE(GROUP_CONCAT(DISTINCT
#         CASE WHEN f.stress >= 80
#              THEN f.timestamp || '|' || f.stress || '|' || f.zone_type
#         END
#     ), ',', ';') AS stress_event_group,

#     REPLACE(GROUP_CONCAT(DISTINCT
#         CASE WHEN f.zone_type = 'unfamiliar'
#              THEN f.timestamp || '|' || f.ppg_threat_detected || '|' || f.imu_danger_level
#                   || '|' || f.hrv || '|' || f.stress || '|' || f.zone_type
#         END
#     ), ',', ';') AS unfamiliar_event_group,

#     REPLACE(GROUP_CONCAT(DISTINCT
#     CASE
#         WHEN ((f.ppg_threat_detected >= 80) +
#               (f.imu_danger_level >= 4) +
#               (f.hrv >= 120) +
#               (f.hrv <= 40) +
#               (f.stress >= 80) +
#               (f.zone_type = 'unfamiliar')) >= 2
#         THEN f.timestamp || '|' || f.ppg_threat_detected || '|' || f.imu_danger_level
#              || '|' || f.hrv || '|' || f.stress || '|' || f.zone_type
#     END
# ), ',', ';') AS 주요_이벤트_타임라인,

#     -- watch 구간은 별도 서브쿼리에서 DISTINCT + REPLACE
#     (
#       SELECT REPLACE(
#         GROUP_CONCAT(DISTINCT disconnect_time || '->' || reconnect_time),
#         ',', ';'
#       )
#       FROM watch_transitions wt
#       WHERE wt.protectee_id = f.protectee_id
#     ) AS watch_connection_transitions

# FROM filtered f
# JOIN users u ON f.protectee_id = u.id
# JOIN max_stress_data msd ON msd.protectee_id = f.protectee_id
# JOIN params p  
# GROUP BY f.protectee_id, u.name, msd.max_stress, msd.max_stress_time;




# """)

# def get_daily_data(name: str, date: str):
#      with engine.connect() as conn:
#         result = conn.execute(query_template, {"name": name, "date": date})
#         row = result.fetchone()
#         return row 
