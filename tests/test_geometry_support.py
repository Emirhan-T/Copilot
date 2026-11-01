from viraj import analyze_xy
from viraj.synthetic import make_road


def test_sparse_corner_is_flagged_descriptively():
    result = analyze_xy([[0, 0], [100, 0], [100, 100]])
    assert len(result.bends) == 1
    assert result.bends[0]["geometry_support"] == "sparse"
    assert result.bends[0]["source_vertex_count"] == 1
    assert result.bends[0]["source_max_segment_m"] == 100


def test_dense_known_arc_has_multiple_source_vertices():
    road = make_road([(100, 0), (100, 1 / 60), (100, 0)])
    result = analyze_xy(road.xy)
    assert result.bends[0]["geometry_support"] == "multi_vertex"
    assert result.quality["sparse_geometry_bend_count"] == 0
