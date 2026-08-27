import { createContext, useCallback, useContext } from "react";

const NavigationContext = createContext(() => {});

export function NavigationProvider({ navigate, children }) {
  return (
    <NavigationContext.Provider value={navigate}>
      {children}
    </NavigationContext.Provider>
  );
}

export function AppLink({ to, onClick, children, ...props }) {
  const navigate = useContext(NavigationContext);
  const handleClick = useCallback(
    (event) => {
      onClick?.(event);
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      event.preventDefault();
      navigate(to);
    },
    [navigate, onClick, to],
  );

  return <a href={to} onClick={handleClick} {...props}>{children}</a>;
}
