package com.group.resv.railway;

import com.group.resv.railway.domain.Station;
import com.group.resv.railway.domain.Train;
import com.group.resv.railway.domain.TrainStop;
import com.group.resv.railway.domain.Trip;
import com.group.resv.railway.repo.StationRepository;
import com.group.resv.railway.repo.TrainRepository;
import com.group.resv.railway.repo.TrainStopRepository;
import com.group.resv.railway.repo.TripClassRepository;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 铁路数据的对外视图拼装（列车摘要 / 经停序列 / 运行日详情）。
 *
 * <p>抽出成独立组件的原因：管理端（{@link RailwayAdminController}）与业务侧只读接口
 * （{@link TicketController} 的 /api/ticket/trains、/api/ticket/trips，供 Agent 调用）
 * 需要同一份结构。各写一份的后果是两边慢慢漂移，前端拿到两种形状。
 */
@Component
public class RailwayView {

    private final TrainRepository trainRepository;
    private final StationRepository stationRepository;
    private final TrainStopRepository trainStopRepository;
    private final TripClassRepository tripClassRepository;

    public RailwayView(TrainRepository trainRepository,
                       StationRepository stationRepository,
                       TrainStopRepository trainStopRepository,
                       TripClassRepository tripClassRepository) {
        this.trainRepository = trainRepository;
        this.stationRepository = stationRepository;
        this.trainStopRepository = trainStopRepository;
        this.tripClassRepository = tripClassRepository;
    }

    /** 用 HashMap 而非 Map.of——首站的到达时间本来就可以为 null，Map.of 遇到 null 会抛 NPE。 */
    public Map<String, Object> trainView(Train t) {
        Map<String, Object> m = new HashMap<>();
        m.put("id", t.getId());
        m.put("code", t.getCode());
        m.put("name", t.getName());
        m.put("kind", t.getKind());
        return m;
    }

    public Map<String, Object> brief(Trip t) {
        Train train = trainRepository.findById(t.getTrainId()).orElse(null);
        Map<String, Object> m = new HashMap<>();
        m.put("id", t.getId());
        m.put("travelDate", t.getTravelDate());
        m.put("status", t.getStatus());
        m.put("trainId", t.getTrainId());
        m.put("trainCode", train == null ? null : train.getCode());
        return m;
    }

    public List<Map<String, Object>> stopViews(Long trainId) {
        return trainStopRepository.findByTrainIdOrderBySeqAsc(trainId).stream()
                .map(this::stopView)
                .toList();
    }

    private Map<String, Object> stopView(TrainStop st) {
        Station s = stationRepository.findById(st.getStationId()).orElse(null);
        Map<String, Object> m = new HashMap<>();
        m.put("seq", st.getSeq());
        m.put("stationId", st.getStationId());
        m.put("station", s == null ? String.valueOf(st.getStationId()) : s.getName());
        m.put("arrive", st.getArriveTime() == null ? null : st.getArriveTime().toString());
        m.put("depart", st.getDepartTime() == null ? null : st.getDepartTime().toString());
        return m;
    }

    public Map<String, Object> detail(Trip trip) {
        Train train = trainRepository.findById(trip.getTrainId()).orElse(null);
        List<Map<String, Object>> classes = tripClassRepository.findByTripIdOrderByIdAsc(trip.getId()).stream()
                .map(c -> Map.<String, Object>of("seatClass", c.getSeatClass(),
                        "priceCents", c.getPriceCents(), "totalSeats", c.getTotalSeats()))
                .toList();
        Map<String, Object> m = new HashMap<>();
        m.put("tripId", trip.getId());
        m.put("travelDate", trip.getTravelDate());
        m.put("trainCode", train == null ? null : train.getCode());
        m.put("classes", classes);
        m.put("stops", stopViews(trip.getTrainId()));
        return m;
    }
}
