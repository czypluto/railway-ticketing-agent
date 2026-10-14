package com.group.resv.config;

import com.group.resv.domain.Contact;
import com.group.resv.domain.User;
import com.group.resv.repo.ContactRepository;
import com.group.resv.repo.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 空表时播种演示用户，并在每次启动时补齐演示账号的档案字段。
 * 铁路线路数据由 railway.RailwayDataInitializer 播种。
 */
@Component
@Order(10)
public class DataInitializer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DataInitializer.class);

    /**
     * 演示账号档案。
     * - 部门：RAG 行级可见性过滤的依据（缺了就只能是公开文档）
     * - 年龄 / 学生身份：儿童票、学生票的确定性校验依据（缺了这两种票一定被拒）
     * 证件号为演示用合成数据，非真实身份信息。
     */
    private record DemoUser(String username, String name, String role, String department,
                            String position, String idNo, Integer age, boolean student) {
    }

    private static final Map<String, DemoUser> DEMO_USERS = Map.of(
            "wangzong", new DemoUser("wangzong", "王建国", "ADMIN",
                    "综合管理部", "部门正职", "110101198001010011", 46, false),
            "zhanggong", new DemoUser("zhanggong", "张伟", "EMPLOYEE",
                    "技术部", "高级工程师", "110101199303030022", 33, false),
            "lizhu", new DemoUser("lizhu", "李琳", "EMPLOYEE",
                    "人事部", "实习生", "110101200405050033", 22, true));

    private final UserRepository userRepository;
    private final ContactRepository contactRepository;
    private final PasswordEncoder passwordEncoder;

    public DataInitializer(UserRepository userRepository, ContactRepository contactRepository,
                           PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.contactRepository = contactRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (userRepository.count() > 0) {
            backfillDemoUsers();
            seedDemoContacts();
            return;
        }
        LocalDateTime now = LocalDateTime.now();
        List<User> users = DEMO_USERS.values().stream().map(this::user).toList();
        for (User u : users) {
            u.setCreatedAt(now);
        }
        userRepository.saveAll(users);
        log.info("已播种 {} 个演示用户（密码 123456）", users.size());
        seedDemoContacts();
    }

    private User user(DemoUser d) {
        User u = new User();
        u.setUsername(d.username());
        u.setPassword(passwordEncoder.encode("123456"));
        u.setName(d.name());
        u.setRole(d.role());
        u.setDepartment(d.department());
        u.setPosition(d.position());
        u.setIdType("身份证");
        u.setIdNo(d.idNo());
        u.setAge(d.age());
        u.setStudent(d.student());
        return u;
    }

    /** 老库补齐部门/职级/证件/年龄/学生身份。幂等：只补空值，不覆盖已有数据。 */
    private void backfillDemoUsers() {
        List<User> changed = new ArrayList<>();
        for (User u : userRepository.findAll()) {
            DemoUser d = DEMO_USERS.get(u.getUsername());
            if (d == null) {
                continue;
            }
            boolean dirty = false;
            if (isBlank(u.getDepartment())) {
                u.setDepartment(d.department());
                dirty = true;
            }
            if (isBlank(u.getPosition())) {
                u.setPosition(d.position());
                dirty = true;
            }
            if (isBlank(u.getIdNo())) {
                u.setIdNo(d.idNo());
                dirty = true;
            }
            if (u.getAge() == null) {
                u.setAge(d.age());
                dirty = true;
            }
            if (u.getStudent() == null) {
                u.setStudent(d.student());
                dirty = true;
            }
            if (dirty) {
                changed.add(u);
            }
        }
        if (!changed.isEmpty()) {
            userRepository.saveAll(changed);
            log.info("已补齐 {} 个演示账号的档案字段", changed.size());
        }
    }

    /**
     * 播种一个儿童乘车人，用于演示「儿童票 6-14 周岁半价」这条确定性规则。
     * 下单时前端/Agent 传 contactId，年龄取自该联系人。
     */
    private void seedDemoContacts() {
        if (contactRepository.count() > 0) {
            return;
        }
        Long zhangId = userRepository.findByUsername("zhanggong").map(User::getId).orElse(null);
        if (zhangId == null) {
            return;
        }
        Contact c = new Contact();
        c.setUserId(zhangId);
        c.setName("张小雨");
        c.setIdType("身份证");
        c.setIdNo("110101201801010044");
        c.setPhone("13800000000");
        c.setAge(8);
        contactRepository.save(c);
        log.info("已播种 1 个演示乘车人（8 岁，用于儿童票演示）");
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
