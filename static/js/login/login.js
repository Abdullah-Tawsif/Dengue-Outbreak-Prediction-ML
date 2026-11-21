
const form = document.getElementById("loginForm");

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
