with source as (
	 select 
	 	hdc.id,
	 	hdmi.str_member_id 
	 from healthcare_data_chart hdc 
	 join healthcare_data_member_info hdmi on hdmi.chart_id = hdc.id
	 where hdc.project_id = 2457 and 
	     hdc.id in (
	     	'19040498','19745513'
	 )
)
SELECT 
	tcd.str_member_id,
    CASE -- replace with actual project s3 path
        WHEN hdcf.file = '' THEN 's3://epp-prod-file-bucket/processed/1-Humana/2630-Confidence_Score_2020/Chart/charts_20260312/A021-000048_2026012611.none' --static value
        ELSE 's3://epp-prod-data-bucket/media/private/' || hdcf.file 
    END as file_source,
    CASE -- replace with actual project s3 path
        WHEN hdcf.is_main THEN 'Chart/charts_20260717/' || hdcf.original_file_name 
        ELSE 'Chart/charts_20260717/' || 
              REGEXP_REPLACE(FIRST_VALUE(hdcf.original_file_name) OVER (PARTITION BY hdcf.chart_id ORDER BY hdcf.is_main DESC NULLS LAST), '\.(none|pdf|xml)$', '') || '/' || hdcf.original_file_name 
    END as file_dest
FROM source tcd
join healthcare_data_chart hdc on hdc.id = tcd.id and hdc.project_id = 2457
JOIN healthcare_data_chartfile hdcf ON hdcf.chart_id = hdc.id
ORDER BY hdcf.chart_id, hdcf.is_main DESC;






----- for wellvana vendor audit (since cf doesnot have all the values)
with source as (
	select id from healthcare_data_chart hdc 
	where hdc.project_id = 2130 and 
		hdc.id in (
		)
)
SELECT 
    's3://epp-prod-data-bucket/media/private/' || hdc.pdf  as file_source,
    'Chart/charts_20260731/' || hdmi.source_file  as file_dest
FROM source tcd
join healthcare_data_chart hdc on hdc.id = tcd.id and hdc.project_id = 2130
join healthcare_data_member_info hdmi on hdmi.chart_id = tcd.id;