from qtx_core.qtx_parser import parse_qtx_text


def _record(section, prefix, name, low, points):
    values=','.join(['25']*points)
    return f'''[{section}],
{prefix}_NAME={name},
{prefix}_GUID={name}-guid,
{prefix}_REFLLOW={low},
{prefix}_REFLINTERVAL=10,
{prefix}_REFLPOINTS={points},
{prefix}_R={values},
'''


def test_section_trailing_comma_and_mixed_wavelength_grids():
    text=_record('STANDARD_DATA 0','STD','Standard',400,31)
    text+=_record('BATCH_DATA','BAT','Batch 1',360,35)
    samples=parse_qtx_text(text,'production.qtx')
    assert [s.display_name for s in samples] == ['Standard','Batch 1']
    assert samples[0].wavelengths == tuple(range(400,701,10))
    assert samples[1].wavelengths == tuple(range(360,701,10))
