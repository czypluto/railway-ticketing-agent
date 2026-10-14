package com.group.resv.config;

import com.group.resv.domain.User;
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

/** 空表时播种演示用户，并在每次启动时补齐演示用户的部门/职级。铁路线路数据由 railway.RailwayDataInitializer 播种。 */
@Component
@Order(10)
public class DataInitializer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DataInitializer.class);

    /** 演示账号 -> {部门, 职级}。部门用于 RAG 行级可见性过滤，缺了就只能是公开文档。 */
    private static final Map<String, String[]> DEMO_ORG = Map.of(
            "wangzong", new String[]{"综合管理部", "部门正职"},
            "zhanggong", new String[]{"技术部", "高级工程师"},
            "lizhu", new String[]{"人事部", "实习生"});

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public DataInitializer(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (userRepository.count() > 0) {
            backfillOrgInfo();
            return;
        }
        LocalDateTime now = LocalDateTime.now();
        List<User> users = List.of(
                user("wangzong", "王建国", "ADMIN"),
                user("zhanggong", "张伟", "EMPLOYEE"),
                user("lizhu", "李琳", "EMPLOYEE"));
        for (User u : users) {
            u.setCreatedAt(now);
        }
        userRepository.saveAll(users);
        log.info("已播种 {} 个演示用户（密码 123456）", users.size());
    }

    /**
     * 老库补齐部门/职级。历史演示数据只写了 role，department 为空会让
     * Python 侧的部门可见性过滤退化成"只能看 all"，行级权限形同失效。
     * 幂等：只补空值，不覆盖已有数据。
     */
    private void backfillOrgInfo() {
        List<User> changed = new ArrayList<>();
        for (User u : userRepository.findAll()) {
            String[] org = DEMO_ORG.get(u.getUsername());
            if (org == null) {
                continue;
            }
            boolean dirty = false;
            if (isBlank(u.getDepartment())) {
                u.setDepartment(org[0]);
                dirty = true;
            }
            if (isBlank(u.getPosition())) {
                u.setPosition(org[1]);
                dirty = true;
            }
            if (dirty) {
                changed.add(u);
            }
        }
        if (!changed.isEmpty()) {
            userRepository.saveAll(changed);
            log.info("已补齐 {} 个演示账号的部门/职级", changed.size());
        }
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }

    private User user(String username, String name, String role) {
        User u = new User();
        u.setUsername(username);
        u.setPassword(passwordEncoder.encode("123456"));
        u.setName(name);
        u.setRole(role);
        String[] org = DEMO_ORG.get(username);
        if (org != null) {
            u.setDepartment(org[0]);
            u.setPosition(org[1]);
        }
        return u;
    }
}
