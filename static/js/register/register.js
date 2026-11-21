
const form = document.getElementById("registerForm");
const password = document.getElementById("password");
const confirmPassword = document.getElementById("confirmPassword");
const meterBar = document.getElementById("meterBar");
const matchStatus = document.getElementById("matchStatus");

document.querySelectorAll(".eye-btn").forEach((button) => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.target);

        input.type = input.type === "password" ? "text" : "password";

        button.setAttribute(
            "aria-label",
            input.type === "password" ? "Show password" : "Hide password"
        );
    });
});

function updatePasswordStrength() {
    let score = 0;

    if (password.value.length >= 8) score++;
    if (/[A-Z]/.test(password.value)) score++;
    if (/[0-9]/.test(password.value)) score++;
    if (/[^A-Za-z0-9]/.test(password.value)) score++;

    meterBar.style.width = `${score * 25}%`;

    meterBar.style.background =
        score < 2 ? "#9e1b32" :
        score < 4 ? "#c56b78" :
        "#7eb78f";
}

function checkPasswordMatch() {
    if (!confirmPassword.value) {
        matchStatus.textContent = "";
        confirmPassword.setCustomValidity("");
        return;
    }

    if (password.value === confirmPassword.value) {
        matchStatus.textContent = "✓ Passwords match";
        matchStatus.style.color = "#8fc59d";
        confirmPassword.setCustomValidity("");
    } else {
        matchStatus.textContent = "Passwords do not match";
        matchStatus.style.color = "#e88998";
        confirmPassword.setCustomValidity("Passwords do not match.");
    }
}

password.addEventListener("input", () => {
    updatePasswordStrength();
    checkPasswordMatch();
});

confirmPassword.addEventListener("input", checkPasswordMatch);
