-- For chart file source & destination extraction:
with source as (
 select id from healthcare_data_chart hdc 
 where hdc.project_id = <project_id> and 
     hdc.id in (<chart_ids>)
)
SELECT 
    CASE -- replace with actual project s3 path
        WHEN hdcf.file = '' THEN 's3://epp-prod-file-bucket/processed/1-Humana/2630-Confidence_Score_2020/Chart/charts_20260312/A021-000048_2026012611.none' --static value
        ELSE 's3://epp-prod-data-bucket/media/private/' || hdcf.file 
    END as file_source,
    CASE -- replace with actual project s3 path
        WHEN hdcf.is_main THEN 's3://epp-prod-file-bucket/processing/332-NLP_Test_Client/2861-LLM_Training_Goldset_2026/Chart/charts_20260529/' || hdcf.original_file_name 
        ELSE 's3://epp-prod-file-bucket/processing/332-NLP_Test_Client/2861-LLM_Training_Goldset_2026/Chart/charts_20260529/' || 
              REGEXP_REPLACE(FIRST_VALUE(hdcf.original_file_name) OVER (PARTITION BY hdcf.chart_id ORDER BY hdcf.is_main DESC NULLS LAST), '\.(none|pdf|xml)$', '') || '/' || hdcf.original_file_name 
    END as file_dest
FROM source tcd
join healthcare_data_chart hdc on hdc.id = tcd.id and hdc.project_id = <project_id>
JOIN healthcare_data_chartfile hdcf ON hdcf.chart_id = hdc.id
ORDER BY hdcf.chart_id, hdcf.is_main DESC;