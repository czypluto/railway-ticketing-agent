package com.group.resv.railway;

import com.group.resv.common.ApiResult;
import com.group.resv.common.BizException;
import com.group.resv.railway.domain.Station;
import com.group.resv.railway.domain.Train;
import com.group.resv.railway.domain.TrainStop;
import com.group.resv.railway.domain.Trip;
import com.group.resv.railway.domain.TripClass;
import com.group.resv.railway.repo.StationRepository;
import com.group.resv.railway.repo.TrainRepository;
import com.group.resv.railway.repo.TrainStopRepository;
import com.group.resv.railway.repo.TripClassRepository;
import com.group.resv.railway.repo.TripRepository;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.LocalTime;
import java.util.List;
import java.util.Map;

/**
 * 资源管理（对应“车次/班次/资源管理”）：列车、车站、停站序列、运行日与席别库存。
 * 演示与运维用；正式上建议独立 ADMIN 权限控制（role=ADMIN）。
 */
@RestController
@RequestMapping("/api/rail")
@PreAuthorize("hasRole('ADMIN')")
@Tag(name = "铁路资源管理（管理端）", description = "车站 / 列车 / 停站 / 运行日 / 席别，仅 ADMIN")
@Transactional
public class RailwayAdminController {

    private final TrainRepository trainRepository;
    private final StationRepository stationRepository;
    private final TrainStopRepository trainStopRepository;
    private final TripRepository tripRepository;
    private final TripClassRepository tripClassRepository;
    private final AdminConfirmService adminConfirm;

    public RailwayAdminController(TrainRepository trainRepository,
                                  StationRepository stationRepository,
                                  TrainStopRepository trainStopRepository,
                                  TripRepository tripRepository,
                                  TripClassRepository tripClassRepository,
                                  AdminConfirmService adminConfirm) {
        this.trainRepository = trainRepository;
        this.stationRepository = stationRepository;
        this.trainStopRepository = trainStopRepository;
        this.tripRepository = tripRepository;
        this.tripClassRepository = tripClassRepository;
        this.adminConfirm = adminConfirm;
    }

    // ---------- 高危操作二次确认 ----------
    public record ConfirmBody(String op, Long target) {
    }

    @PostMapping("/confirm/request")
    public ApiResult<Map<String, Object>> confirmRequest(@RequestBody ConfirmBody body) {
        AdminConfirmService.ConfirmTicket t = adminConfirm.issue(body.op(), body.target());
        return ApiResult.ok(Map.of("token", t.token(), "expiresSeconds", t.expiresSeconds()));
    }

    private void requireConfirm(String op, Long target, String token) {
        if (!adminConfirm.verifyAndConsume(op, target, token)) {
            throw new BizException(403, "高危操作需二次确认：请先 POST /api/rail/confirm/request 获取一次性确认码，并以请求头 X-Confirm-Token 携带");
        }
    }

    // ---------- 车站 ----------
    public record StationBody(String code, String name) {
    }

    @GetMapping("/stations")
    public ApiResult<List<Station>> stations(@RequestParam(required = false) String kw) {
        List<Station> list = kw == null || kw.isBlank()
                ? stationRepository.findAll()
                : stationRepository.findByNameContainingOrderByIdAsc(kw);
        return ApiResult.ok(list);
    }

    @PostMapping("/stations")
    public ApiResult<Station> addStation(@RequestBody StationBody body) {
        if (body.name() == null || body.name().isBlank()) throw new BizException(400, "站名不能为空");
        if (stationRepository.findByName(body.name()).isPresent()) throw new BizException(409, "站点已存在");
        Station s = new Station();
        s.setName(body.name());
        s.setCode(body.code() == null ? body.name() : body.code());
        return ApiResult.ok(stationRepository.save(s));
    }

    // ---------- 列车 ----------
    public record TrainBody(String code, String name, String kind) {
    }

    @GetMapping("/trains")
    public ApiResult<List<Train>> trains() {
        return ApiResult.ok(trainRepository.findAll());
    }

    @PostMapping("/trains")
    public ApiResult<Train> addTrain(@RequestBody TrainBody body) {
        if (body.code() == null || body.code().isBlank()) throw new BizException(400, "车次号不能为空");
        if (trainRepository.findByCode(body.code()).isPresent()) throw new BizException(409, "车次已存在");
        Train t = new Train();
        t.setCode(body.code());
        t.setName(body.name());
        t.setKind(body.kind());
        return ApiResult.ok(trainRepository.save(t));
    }

    @PutMapping("/trains/{id}")
    public ApiResult<Train> updateTrain(@PathVariable Long id, @RequestBody TrainBody body) {
        Train t = trainRepository.findById(id).orElseThrow(() -> new BizException(404, "车次不存在"));
        if (body.name() != null) t.setName(body.name());
        if (body.kind() != null) t.setKind(body.kind());
        return ApiResult.ok(trainRepository.save(t));
    }

    @DeleteMapping("/trains/{id}")
    public ApiResult<Void> deleteTrain(@PathVariable Long id,
                                       @RequestHeader(value = "X-Confirm-Token", required = false) String confirm) {
        requireConfirm("deleteTrain", id, confirm);
        // 已有运行记录则拒绝物理删除，避免脏数据
        if (!tripRepository.findByTrainIdOrderByTravelDateDesc(id).isEmpty()) {
            throw new BizException(409, "该车次已有运行记录，不可删除");
        }
        trainStopRepository.deleteByTrainId(id);
        trainRepository.deleteById(id);
        return ApiResult.ok();
    }

    // ---------- 停站序列 ----------
    public record StopBody(Long stationId, String arrive, String depart) {
    }

    public record StopsBody(List<StopBody> stops) {
    }

    /** 整体重建某列车的经停序列（按数组顺序赋 seq）。 */
    @PostMapping("/trains/{id}/stops")
    public ApiResult<List<TrainStop>> rebuildStops(@PathVariable Long id, @RequestBody StopsBody body) {
        trainRepository.findById(id).orElseThrow(() -> new BizException(404, "车次不存在"));
        if (body.stops() == null || body.stops().size() < 2) throw new BizException(400, "至少两个停靠站");
        trainStopRepository.deleteByTrainId(id);
        int seq = 0;
        for (StopBody sb : body.stops()) {
            TrainStop st = new TrainStop();
            st.setTrainId(id);
            st.setSeq(seq++);
            st.setStationId(sb.stationId());
            st.setArriveTime(parse(sb.arrive()));
            st.setDepartTime(parse(sb.depart()));
            trainStopRepository.save(st);
        }
        return ApiResult.ok(trainStopRepository.findByTrainIdOrderBySeqAsc(id));
    }

    /** 读取某列车的经停序列（此前只有"整体重建"接口，没有读取接口）。 */
    @GetMapping("/trains/{id}/stops")
    public ApiResult<List<Map<String, Object>>> trainStops(@PathVariable Long id) {
        trainRepository.findById(id).orElseThrow(() -> new BizException(404, "车次不存在"));
        return ApiResult.ok(stopViews(id));
    }

    // ---------- 运行日 + 席别 ----------
    public record ClassBody(String seatClass, long priceCents, int totalSeats) {
    }

    public record TripBody(Long trainId, LocalDate travelDate, List<ClassBody> classes) {
    }

    @PostMapping("/trips")
    public ApiResult<Trip> createTrip(@RequestBody TripBody body) {
        trainRepository.findById(body.trainId()).orElseThrow(() -> new BizException(404, "车次不存在"));
        if (body.travelDate() == null || body.classes() == null || body.classes().isEmpty()) {
            throw new BizException(400, "日期与席别不能为空");
        }
        if (tripRepository.findByTrainIdAndTravelDate(body.trainId(), body.travelDate()).isPresent()) {
            throw new BizException(409, "该车次当天已开行");
        }
        Trip trip = new Trip();
        trip.setTrainId(body.trainId());
        trip.setTravelDate(body.travelDate());
        tripRepository.save(trip);
        for (ClassBody cb : body.classes()) {
            TripClass tc = new TripClass();
            tc.setTripId(trip.getId());
            tc.setSeatClass(cb.seatClass());
            tc.setPriceCents(cb.priceCents());
            tc.setTotalSeats(cb.totalSeats());
            tripClassRepository.save(tc);
        }
        return ApiResult.ok(trip);
    }

    @GetMapping("/trips")
    public ApiResult<List<Map<String, Object>>> trips(
            @RequestParam(required = false) LocalDate date,
            @RequestParam(required = false) Long trainId) {
        List<Trip> list;
        if (trainId != null) {
            list = tripRepository.findByTrainIdOrderByTravelDateDesc(trainId);
        } else if (date != null) {
            list = tripRepository.findByTravelDateOrderByTravelDateAsc(date);
        } else {
            list = tripRepository.findAll();
        }
        return ApiResult.ok(list.stream().map(this::brief).toList());
    }

    @GetMapping("/trips/{id}")
    public ApiResult<Map<String, Object>> tripDetail(@PathVariable Long id) {
        Trip trip = tripRepository.findById(id).orElseThrow(() -> new BizException(404, "车次不存在"));
        return ApiResult.ok(detail(trip));
    }

    @DeleteMapping("/trips/{id}")
    public ApiResult<Void> deleteTrip(@PathVariable Long id,
                                      @RequestHeader(value = "X-Confirm-Token", required = false) String confirm) {
        requireConfirm("deleteTrip", id, confirm);
        tripClassRepository.deleteAll(tripClassRepository.findByTripIdOrderByIdAsc(id));
        tripRepository.deleteById(id);
        return ApiResult.ok();
    }

    private Map<String, Object> brief(Trip t) {
        Train train = trainRepository.findById(t.getTrainId()).orElse(null);
        Map<String, Object> m = new java.util.HashMap<>();
        m.put("id", t.getId());
        m.put("travelDate", t.getTravelDate());
        m.put("status", t.getStatus());
        m.put("trainId", t.getTrainId());
        m.put("trainCode", train == null ? null : train.getCode());
        return m;
    }

    private Map<String, Object> detail(Trip trip) {
        Train train = trainRepository.findById(trip.getTrainId()).orElse(null);
        List<Map<String, Object>> classes = tripClassRepository.findByTripIdOrderByIdAsc(trip.getId()).stream()
                .map(c -> Map.<String, Object>of("seatClass", c.getSeatClass(),
                        "priceCents", c.getPriceCents(), "totalSeats", c.getTotalSeats()))
                .toList();
        List<Map<String, Object>> stops = stopViews(trip.getTrainId());
        Map<String, Object> m = new java.util.HashMap<>();
        m.put("tripId", trip.getId());
        m.put("travelDate", trip.getTravelDate());
        m.put("trainCode", train == null ? null : train.getCode());
        m.put("classes", classes);
        m.put("stops", stops);
        return m;
    }

    /** 经停序列视图。用 HashMap 而非 Map.of——首站的到达时间本来就可以为 null，Map.of 遇到 null 会抛 NPE。 */
    private List<Map<String, Object>> stopViews(Long trainId) {
        return trainStopRepository.findByTrainIdOrderBySeqAsc(trainId).stream()
                .map(st -> {
                    Station s = stationRepository.findById(st.getStationId()).orElse(null);
                    Map<String, Object> m = new java.util.HashMap<>();
                    m.put("seq", st.getSeq());
                    m.put("stationId", st.getStationId());
                    m.put("station", s == null ? String.valueOf(st.getStationId()) : s.getName());
                    m.put("arrive", st.getArriveTime() == null ? null : st.getArriveTime().toString());
                    m.put("depart", st.getDepartTime() == null ? null : st.getDepartTime().toString());
                    return m;
                }).toList();
    }

    private LocalTime parse(String s) {
        if (s == null || s.isBlank()) return null;
        return LocalTime.parse(s.length() == 5 ? s + ":00" : s);
    }
}
