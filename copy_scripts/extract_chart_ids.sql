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



----------- For charts acc to BANDs for PDFs -------------------
with eligible as (
    select distinct
        hdmi.chart_id,
        hdc.pdf_page_count as page_count          
    from healthcare_data_member_info hdmi
    join healthcare_data_chart hdc
        on hdc.id = hdmi.chart_id
    where hdmi.project_id = 2457
        and hdmi.chart_id is not null
        and hdmi.source_file ilike '%.pdf'
        and not exists (
            select 1
            from healthcare_data_chart hdc_o
            where hdc_o.project_id = 2861
                and hdc_o.str_member_id = hdmi.str_member_id
        )
),
banded as (
    select
        chart_id,
        page_count,
        case
            when page_count <= 100                       then 1
            when page_count between 101 and 300          then 2
            when page_count between 301 and 500          then 3
            when page_count between 501 and 700          then 4
        end as band
    from eligible
),
ranked as (
    select
        chart_id,
        band,
        row_number() over (partition by band order by random()) as rn
    from banded
    where band is not null                     -- drops page_count > 700 or null
)
select chart_id, band
from ranked
where rn <= 250                                -- 250 per band = 1000 total
order by band, rn;






---------------- For Molina ------------------------------
with cte as (
	(
		select hdc.id  from healthcare_data_chart hdc 
		join healthcare_data_chartfile cf on hdc.id = cf.chart_id 
		where project_id in (2729,2733) and cf.original_file_name  ilike '%.none'
	--	and not exists (select 1 from healthcare_data_chart hdc_o where hdc_o.project_id = 2861 and hdc_o.str_member_id = hdc.str_member_id )
		order by random()
	)
	union
	(
		select hdmi.chart_id 
		from healthcare_data_member_info hdmi 
		where project_id in (2729,2733) and hdmi.chart_id is not null
		and hdmi.source_file ilike '%.pdf'
		order by random()
	)
)
select distinct id from cte
limit 3000;



------------ for goldset
---------------------------- PDFs ---------------------------------
with eligible as (
    select distinct
        hdmi.chart_id,
        hdc.pdf_page_count as page_count          
    from healthcare_data_member_info hdmi
    join healthcare_data_chart hdc
        on hdc.id = hdmi.chart_id
    where hdmi.project_id = 2457
        and hdmi.chart_id is not null
        and hdmi.source_file ilike '%.pdf'
        and hdc.pdf_page_count between 1 and 700
        and not exists (
            select 1
            from healthcare_data_chart hdc_o
            where hdc_o.project_id in (2861, 3257)
                and hdc_o.str_member_id = hdmi.str_member_id
        )
),
banded as (
    select
        chart_id,
        page_count,
        case
            when page_count <= 100                       then 1
            when page_count between 101 and 300          then 2
            when page_count between 301 and 500          then 3
            when page_count between 501 and 700          then 4
        end as band
    from eligible
),
ranked as (
    select
        chart_id,
        band,
        row_number() over (partition by band order by random()) as rn
    from banded
    where band is not null                     -- drops page_count > 700 or null
)
select chart_id, band
from ranked
where rn <= 125                                -- 250 per band = 1000 total
order by band, rn;



---------------------------- CCDAs ---------------------------------
with eligible as (
    select distinct
        hdmi.chart_id
    from healthcare_data_member_info hdmi
    join healthcare_data_chart hdc
        on hdc.id = hdmi.chart_id
    where hdmi.project_id = 2457
        and hdmi.chart_id is not null
        and hdmi.source_file ilike '%.none'
        and not exists (
            select 1
            from healthcare_data_chart hdc_o
            where hdc_o.project_id in (2861, 3257)
                and hdc_o.str_member_id = hdmi.str_member_id
        )
),
charts as (
	select 
		cf.chart_id,
		count(cf.original_file_name ) as charts_count
	from eligible e
	join healthcare_data_chartfile cf on cf.chart_id = e.chart_id 
	where cf.is_main = False
	group by cf.chart_id 
	having count(cf.original_file_name )>1
	limit 500
)
select chart_id, charts_count
from charts;
