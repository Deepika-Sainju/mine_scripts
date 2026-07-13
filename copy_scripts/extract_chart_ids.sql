-------------- For CCDAs ------------------------------
with cte as (
	select hdc.id, hdc.chart_id, cf.original_file_name  from healthcare_data_chart hdc 
	join healthcare_data_chartfile cf on hdc.id = cf.chart_id 
	where project_id = 2457 and cf.original_file_name  ilike '%.none'
	and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdc.str_member_id )
	order by random()
	limit 1000
)
select distinct id from cte;
--where original_file_name ilike '%.none';




------------ For Both ---------------------------------
with cte as (
	(select hdc.id  from healthcare_data_chart hdc 
	join healthcare_data_chartfile cf on hdc.id = cf.chart_id 
	where project_id = 2457 and cf.original_file_name  ilike '%.none'
--	and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdc.str_member_id )
	order by random()
	limit 2500)
	union
	(select hdmi.chart_id 
	from healthcare_data_member_info hdmi 
	where project_id = 2457 and hdmi.chart_id is not null
	and hdmi.source_file ilike '%.pdf'
	order by random()
	limit 2500)
)
select distinct id from cte;

-- optimized for both
with none_charts as (
    select hdc.id
    from healthcare_data_chart hdc
    where hdc.project_id = 2457
      and exists (
          select 1
          from healthcare_data_chartfile cf
          where cf.chart_id = hdc.id
            and cf.original_file_name ilike '%.none'
      )
      -- and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdc.str_member_id )
    order by random()
    limit 2500
),
pdf_charts as (
    select hdmi.chart_id as id
    from healthcare_data_member_info hdmi
    where hdmi.project_id = 2457
      and hdmi.chart_id is not null
      and hdmi.source_file ilike '%.pdf'
      -- and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdmi.str_member_id )
    order by random()
    limit 2500
)
select id from none_charts
union
select id from pdf_charts;



------------ For PDF ---------------------------------
with cte as (
	select hdmi.chart_id  
	from healthcare_data_member_info hdmi 
	where project_id = 2457 
		and hdmi.chart_id is not null
		and hdmi.source_file ilike '%.pdf'
		and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdmi.str_member_id )
	order by random()
	limit 2500
)
select distinct chart_id from cte;