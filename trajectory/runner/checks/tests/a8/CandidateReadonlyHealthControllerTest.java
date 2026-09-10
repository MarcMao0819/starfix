package fixture;

import org.springframework.test.context.ActiveProfiles;

@ActiveProfiles({"prod", "local", "candidate-readonly"})
class CandidateReadonlyHealthControllerTest {

    void healthOnly() {
        jdbcTemplate.queryForObject("SELECT 1", Integer.class);
    }
}
